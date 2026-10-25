"""Voice entry for questions: PCM in, transcript text out.

The socket only transcribes. The browser sends the completed text to POST /questions, the
same path typed questions use, so answers, clarifications and budget rules are shared.
Captions and transcripts never answer anything by themselves and nothing is persisted.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import Field, TypeAdapter

from app.ai.api_budget import ApiBudget
from app.ai.realtime_transcription import SESSION_CONFIG, upstream_connection
from app.api.v2.editing_contracts import Id
from app.core.config import Settings, local_host
from app.core.errors import AppError
from app.editing.errors import reject
from app.editing.operation_contracts import Contract
from app.structured.service import StructuredDocumentService

CONNECTION_SECONDS = 300
RENEWAL_SECONDS = 105
UTTERANCE_SECONDS = 180
FINAL_WAIT_SECONDS = 15
MAX_UTTERANCES = 64
SEGMENT_SECONDS = 25
CONNECT_PURPOSE = "qa.voice.connect"  # "...connect" prepays 300 s in the cloud budget.
AUDIO_PURPOSE = "qa.voice.audio"
PROMPT = (
    "日本語で、確定済みの帳票への質問を聞き取る音声。"
    "書類名、人や個体の名前、欄名、数値、日付を正確に文字起こしする。"
)


class Begin(Contract):
    type: Literal["utterance.begin"]
    utterance_id: Id


class SegmentBegin(Contract):
    type: Literal["segment.begin"]
    segment_id: Id
    sequence: int = Field(ge=1, le=180, strict=True)


class SegmentEnd(Contract):
    type: Literal["segment.end"]
    segment_id: Id


class End(Contract):
    type: Literal["utterance.end"]
    manifest: list[Id] = Field(min_length=1, max_length=180)


class Simple(Contract):
    type: Literal["speech.stopped", "utterance.cancel", "capture.stop"]


Control: TypeAdapter[Begin | SegmentBegin | SegmentEnd | End | Simple] = TypeAdapter(
    Annotated[Begin | SegmentBegin | SegmentEnd | End | Simple, Field(discriminator="type")]
)


class VoiceInvalid(Exception):
    pass


@dataclass(slots=True)
class Utterance:
    utterance_id: str
    started: float
    order: list[str] = field(default_factory=list)
    texts: dict[str, str | None] = field(default_factory=dict)
    open_segment: str | None = None
    manifest: list[str] | None = None
    samples: int = 0
    final_deadline: float | None = None

    @property
    def text(self) -> str | None:
        if self.manifest is None or any(self.texts.get(s) is None for s in self.manifest):
            return None
        return "".join(self.texts[s] or "" for s in self.manifest)


def transcription_config(keywords: list[str]) -> dict[str, Any]:
    config = copy.deepcopy(SESSION_CONFIG)
    transcription = config["session"]["audio"]["input"]["transcription"]
    transcription["prompt"] = PROMPT
    if keywords:
        transcription["keywords"] = keywords
    return config


class QuestionVoice:
    def __init__(
        self, settings: Settings, budget: ApiBudget, service: StructuredDocumentService
    ) -> None:
        self.settings, self.budget, self.service = settings, budget, service

    @property
    def configured(self) -> bool:
        return self.settings.voice.provider == "openai" and bool(self.settings.ocr.openai_api_key)

    def keywords(self) -> list[str]:
        """Document names and identity values of confirmed documents, for recognition only."""
        result: list[str] = []
        for summary in self.service.list(limit=200):
            names = [summary.get("documentName"), *(i.get("value") for i in summary["identity"])]
            for name in names:
                if (
                    isinstance(name, str)
                    and 0 < len(name) <= 100
                    and not any(c in name for c in "<>\r\n")
                    and name not in result
                ):
                    result.append(name)
        return result[:100]

    def reserve(self) -> None:
        if not self.configured:
            raise reject("VOICE_NOT_CONFIGURED", "音声APIの設定がありません。", 503)
        self.budget.reserve(CONNECT_PURPOSE)

    async def run(self, socket: WebSocket, factory: Any = upstream_connection) -> None:
        accepted = False
        upstream_cm: Any = None
        upstream: Any = None
        opened = time.monotonic()
        active: Utterance | None = None
        utterances: dict[str, Utterance] = {}
        commits: deque[tuple[str, str]] = deque()
        identities: dict[str, str] = {}
        captions: dict[str, str] = {}
        started_ids: set[str] = set()
        lock = asyncio.Lock()
        upstream_lock = asyncio.Lock()
        renewal = asyncio.Event()
        renewal.set()

        async def send(value: dict[str, Any]) -> None:
            async with lock:
                await socket.send_json(value)

        async def open_upstream() -> None:
            nonlocal upstream_cm, upstream, opened
            await asyncio.to_thread(self.reserve)
            opened = time.monotonic()
            upstream_cm = factory(self.settings)
            upstream = await upstream_cm.__aenter__()
            keywords = await asyncio.to_thread(self.keywords)
            await upstream.send(json.dumps(transcription_config(keywords)))
            async with asyncio.timeout(10):
                while True:
                    event = json.loads(await upstream.recv())
                    if event.get("type") == "error":
                        raise VoiceInvalid("Provider rejected transcription configuration")
                    if event.get("type") == "session.updated":
                        return

        async def close_upstream() -> None:
            nonlocal upstream_cm, upstream
            if upstream_cm is not None:
                await upstream_cm.__aexit__(None, None, None)
                upstream_cm, upstream = None, None

        async def complete(utterance: Utterance) -> None:
            text = utterance.text
            if text is None or utterance.utterance_id not in utterances:
                return
            utterances.pop(utterance.utterance_id)
            captions.pop(utterance.utterance_id, None)
            for item_id in [k for k, v in identities.items() if v == utterance.utterance_id]:
                identities.pop(item_id)
            await send(
                {
                    "type": "transcript.completed",
                    "utteranceId": utterance.utterance_id,
                    "text": text.strip(),
                }
            )

        try:
            if (
                not getattr(socket.app.state, "cloud_authenticated", False)
                and not local_host(socket.url.hostname)
            ) or socket.headers.get("origin") not in self.settings.app.frontend_origins:
                await socket.close(code=1008)
                return
            await socket.accept()
            accepted = True
            await open_upstream()
            await send(
                {
                    "type": "ready",
                    "connectionSeconds": CONNECTION_SECONDS,
                    "utteranceSeconds": UTTERANCE_SECONDS,
                }
            )

            async def incoming() -> None:
                nonlocal active
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    async with upstream_lock:
                        if (data := message.get("bytes")) is not None:
                            if active is None or active.open_segment is None:
                                raise VoiceInvalid("PCM without an open segment")
                            active.samples += len(data) // 2
                            if active.samples > 24_000 * UTTERANCE_SECONDS:
                                raise VoiceInvalid("Utterance audio limit exceeded")
                            await upstream.send(
                                json.dumps(
                                    {
                                        "type": "input_audio_buffer.append",
                                        "audio": base64.b64encode(data).decode("ascii"),
                                    }
                                )
                            )
                            continue
                        raw_control = message.get("text", "")
                        if len(raw_control) > 20000:
                            raise VoiceInvalid("Control is too large")
                        control = Control.validate_json(raw_control)
                        if isinstance(control, Begin):
                            if (
                                active is not None
                                or control.utterance_id in started_ids
                                or len(started_ids) >= MAX_UTTERANCES
                            ):
                                raise VoiceInvalid("An utterance is still incomplete")
                            started_ids.add(control.utterance_id)
                            # Renewal happens only at an idle boundary, before new speech.
                            if time.monotonic() - opened > RENEWAL_SECONDS:
                                renewal.clear()
                                await close_upstream()
                                await open_upstream()
                                renewal.set()
                                identities.clear()
                                await send({"type": "connection.renewed"})
                            active = Utterance(control.utterance_id, time.monotonic())
                            utterances[active.utterance_id] = active
                            await send(
                                {"type": "utterance.ready", "utteranceId": active.utterance_id}
                            )
                        elif isinstance(control, SegmentBegin):
                            if active is None or active.open_segment is not None:
                                raise VoiceInvalid("Segment without an utterance")
                            if (
                                control.segment_id in active.texts
                                or control.sequence != len(active.order) + 1
                            ):
                                raise VoiceInvalid("Segment order is invalid")
                            if control.sequence == 1:
                                # One question is one reservation, however many pieces.
                                await asyncio.to_thread(self.budget.reserve, AUDIO_PURPOSE)
                            active.open_segment = control.segment_id
                            active.order.append(control.segment_id)
                            active.texts[control.segment_id] = None
                        elif isinstance(control, SegmentEnd):
                            if active is None or active.open_segment != control.segment_id:
                                raise VoiceInvalid("Segment end without a matching begin")
                            active.open_segment = None
                            commits.append((active.utterance_id, control.segment_id))
                            await upstream.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        elif isinstance(control, End):
                            if active is None or active.open_segment is not None:
                                raise VoiceInvalid("End without a closed utterance")
                            if control.manifest != active.order:
                                raise VoiceInvalid("Manifest does not match the sent segments")
                            active.manifest = list(control.manifest)
                            active.final_deadline = time.monotonic() + FINAL_WAIT_SECONDS
                            ended = active
                            active = None
                            await complete(ended)
                        elif control.type == "utterance.cancel":
                            if active is not None:
                                await upstream.send(
                                    json.dumps({"type": "input_audio_buffer.clear"})
                                )
                                utterances.pop(active.utterance_id, None)
                                captions.pop(active.utterance_id, None)
                                active = None
                        elif control.type == "capture.stop":
                            return

            async def outgoing() -> None:
                while True:
                    source = upstream
                    try:
                        raw = await source.recv()
                    except Exception:
                        if not renewal.is_set():
                            await renewal.wait()
                        if source is not upstream and upstream is not None:
                            continue
                        raise
                    event = json.loads(raw)
                    kind, item_id = event.get("type"), event.get("item_id")
                    if kind == "input_audio_buffer.committed":
                        if not commits or not isinstance(item_id, str) or item_id in identities:
                            raise VoiceInvalid("Invalid commit order")
                        uid, sid = commits.popleft()
                        identities[item_id] = f"{uid}\n{sid}"
                    elif kind == "conversation.item.input_audio_transcription.completed":
                        owner = identities.get(item_id)
                        if owner is None or not isinstance(event.get("transcript"), str):
                            raise VoiceInvalid("Unowned transcript")
                        uid, sid = owner.split("\n", 1)
                        utterance = utterances.get(uid)
                        if utterance is None:
                            identities.pop(item_id)
                            continue  # Cancelled utterances drop late transcripts.
                        utterance.texts[sid] = event["transcript"]
                        await complete(utterance)
                    elif kind == "conversation.item.input_audio_transcription.delta":
                        delta = event.get("delta")
                        owner = identities.get(item_id)
                        caption_uid = (
                            owner.split("\n", 1)[0]
                            if owner
                            else (active.utterance_id if active else None)
                        )
                        if (
                            isinstance(delta, str)
                            and len(delta) <= 20000
                            and caption_uid in utterances
                        ):
                            captions[caption_uid] = captions.get(caption_uid, "") + delta
                            if len(captions[caption_uid]) > 20000:
                                raise VoiceInvalid("Live caption limit exceeded")
                            await send(
                                {
                                    "type": "transcript.delta",
                                    "utteranceId": caption_uid,
                                    "delta": delta,
                                }
                            )
                    elif kind in {"error", "conversation.item.input_audio_transcription.failed"}:
                        raise VoiceInvalid("Transcription failed")

            async def watchdog() -> None:
                while True:
                    await asyncio.sleep(0.25)
                    now = time.monotonic()
                    if now - opened >= CONNECTION_SECONDS and active is None and not any(
                        u.final_deadline is not None for u in utterances.values()
                    ):
                        raise reject(
                            "VOICE_IDLE",
                            "しばらく話がなかったため音声入力を止めました。"
                            "続けるにはマイクのボタンを押してください。",
                            409,
                        )
                    if (
                        now - opened >= CONNECTION_SECONDS
                        or (active is not None and now - active.started > UTTERANCE_SECONDS)
                        or any(
                            u.final_deadline is not None and u.final_deadline <= now
                            for u in utterances.values()
                        )
                    ):
                        raise reject(
                            "VOICE_DEADLINE", "音声の取得又は認識が制限時間を超えました。", 409
                        )

            tasks = [asyncio.create_task(coro()) for coro in (incoming, outgoing, watchdog)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception as error:
            if accepted:
                with suppress(RuntimeError, WebSocketDisconnect, OSError):
                    await send(
                        {
                            "type": "error",
                            "code": error.code
                            if isinstance(error, AppError)
                            else "VOICE_STREAM_FAILED",
                            "message": error.detail
                            if isinstance(error, AppError)
                            else "音声取得を停止しました。聞き取れた質問は送信していません。",
                        }
                    )
        finally:
            with suppress(Exception):
                await close_upstream()
            if accepted:
                with suppress(RuntimeError, WebSocketDisconnect, OSError):
                    await socket.close(code=1000)
