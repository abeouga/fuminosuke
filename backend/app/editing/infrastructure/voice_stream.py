"""Authenticated PCM capture, bounded renewal, ordered transcription and shared commands."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from app.ai.api_budget import ApiBudget
from app.ai.realtime_transcription import transcription_config, upstream_connection
from app.api.v2.voice_contracts import Begin, Control, End, SegmentBegin, SegmentEnd
from app.assistant.stream_models import VoiceStream
from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import utc_now
from app.editing.domain.utterance import (
    AudioInvalid,
    Utterance,
    UtteranceBoundaryPolicy,
    is_noise,
)
from app.editing.errors import reject

JST = timezone(timedelta(hours=9))

def day_start() -> datetime:
    """Start of the current JST calendar day, in UTC."""
    now = datetime.now(JST)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def used_today(session: Any) -> int:
    # Connections are charged their reserved 300 s until closed, so an open or
    # crashed connection never makes the remaining time look larger than it is.
    return int(
        session.scalar(
            select(func.sum(VoiceStream.charged_seconds)).where(
                VoiceStream.created_at >= day_start()
            )
        )
        or 0
    )


def binding(context: dict[str, Any]) -> dict[str, Any]:
    scope = context["scopeContext"]
    return {
        "expectedRevision": scope["resourceRevision"],
        **{
            k: scope[k]
            for k in ("contextRevision", "interactionEpoch", "catalogRevision", "scopeDigest")
        },
    }


class VoiceConnections:
    def __init__(self, runtime: Any, settings: Settings, budget: ApiBudget) -> None:
        self.runtime, self.settings, self.budget = runtime, settings, budget
        self.sockets: dict[str, WebSocket] = {}
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.generations: dict[str, tuple[Any, ...]] = {}
        self.policy = UtteranceBoundaryPolicy()

    def reserve(self, session_id: str, token: str) -> str:
        context = self.runtime.sessions.context(session_id, token)
        scope = context["scopeContext"]
        self.generations[session_id] = tuple(
            scope[k] for k in ("contextRevision", "interactionEpoch", "scopeDigest")
        )
        if context["scopeContext"]["mode"] not in {"continuous", "local"}:
            raise reject("MODE_MISMATCH", "連続又は局所音声モードを指定してください。", 409)
        if self.settings.voice.provider != "openai" or not self.settings.ocr.openai_api_key:
            raise reject("VOICE_NOT_CONFIGURED", "音声APIの設定がありません。", 503)
        with self.runtime.sessions.database.transaction() as session:
            limit = self.settings.voice.daily_seconds
            if used_today(session) + 300 > limit:
                raise reject(
                    "VOICE_TIME_EXHAUSTED",
                    f"本日の音声入力時間の上限、{limit // 60}分に達しました。"
                    "明日になると再び使えます。手動の編集は続けられます。",
                    429,
                )
            row = VoiceStream(
                session_id=session_id,
                state="CONNECTED",
                reserved_seconds=300,
                charged_seconds=300,
                expires_at=utc_now(),
                connected_at=utc_now(),
            )
            if (
                isinstance(self.budget, ApiBudget)
                and self.budget.database is self.runtime.sessions.database
            ):
                row.connect_call_id = self.budget.reserve_in(session, "editing.v2.voice.connect")
            else:
                self.budget.reserve("editing.v2.voice.connect")
            session.add(row)
            session.flush()
            connection_id = row.id
        return connection_id

    def record(self, session_id: str, **entry: Any) -> None:
        """Opt-in test log (VOICE_TRANSCRIPT_LOG): text and outcome, local file only."""
        if not self.settings.voice.transcript_log:
            return
        path = self.settings.storage.data_dir / "logs" / "voice-transcripts.jsonl"
        with suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                line = {"at": utc_now().isoformat(), "sessionId": session_id, **entry}
                file.write(json.dumps(line, ensure_ascii=False) + "\n")

    def finish(self, connection_id: str, elapsed: float) -> None:
        with self.runtime.sessions.database.transaction() as session:
            row = session.get(VoiceStream, connection_id)
            if row:
                row.state, row.closed_at = "CLOSED", utc_now()
                row.charged_seconds = min(300, max(1, math.ceil(elapsed)))

    def keywords(self, context: dict[str, Any], session_id: str, token: str) -> list[str]:
        scope, result = context["scopeContext"], []
        allowed = None
        if scope["resource"]["kind"] == "template" and scope["selectedTableId"]:
            from app.editing.semantic_commands import section_cells

            with self.runtime.sessions.database.session() as session:
                owner = self.runtime.sessions.owner(session, session_id, token)
                catalog = self.runtime.sessions.catalogs.build(session, owner)
                cells = section_cells(catalog.state["layout"], scope["selectedTableId"])
                allowed = {
                    t.handle
                    for t in catalog.targets.values()
                    if t.entity_id == scope["selectedTableId"]
                    or (t.kind == "cell" and t.entity_id in cells)
                }
                for t in catalog.targets.values():
                    if t.detail.get("ownerHandle") in allowed:
                        allowed.add(t.handle)
                        if t.detail.get("meaningHandle"):
                            allowed.add(t.detail["meaningHandle"])
        in_scope: list[str] = []
        for target in context["catalog"]["targets"]:
            if scope["mode"] == "local" and target["handle"] != scope["localTargetHandle"]:
                continue
            if allowed is not None and target["handle"] not in allowed:
                continue
            cell = target.get("recordCell") or {}
            if (
                allowed is None
                and scope["selectedTableId"]
                and cell.get("tableId") != scope["selectedTableId"]
            ):
                continue
            in_scope.append(target["handle"])
            for name in [
                target["name"],
                *target.get("aliases", []),
                cell.get("tableName"),
                cell.get("columnName"),
            ]:
                if (
                    name
                    and len(name) <= 100
                    and not any(c in name for c in "<>\r\n")
                    and name not in result
                ):
                    result.append(name)
        # Then the keywords the template settings give each field in scope (bull names
        # and the like), so a spoken keyword is transcribed in its registered spelling.
        for word in self.field_keywords(context, in_scope):
            if word not in result:
                result.append(word)
        # Then words already written on the document (names such as こよみ), which a
        # transcriber otherwise turns into common homophones. Numbers are not hints.
        for target in context["catalog"]["targets"]:
            value = target.get("currentValue")
            if (
                isinstance(value, str)
                and 2 <= len(value) <= 20
                and not any(c.isdigit() for c in value)
                and not any(c in value for c in "<>\r\n")
                and value not in result
            ):
                result.append(value)
        return result[:100]

    def field_keywords(self, context: dict[str, Any], handles: list[str]) -> list[str]:
        """Enabled keywords of the given OCR fields, as fixed when the job was accepted."""
        resource = context["scopeContext"]["resource"]
        if resource["kind"] != "ocr" or not handles:
            return []
        from app.db.models import ProcessingJob
        from app.domain.template_candidates import normalize_candidate_target
        from app.services.template_field_hint_service import snapshot_hints

        with self.runtime.sessions.database.session() as session:
            job = session.get(ProcessingJob, resource["id"])
            hints = snapshot_hints(job.config_snapshot_json or {}) if job is not None else {}
        if not hints:
            return []
        entity = {b["handle"]: b["entityId"] for b in context.get("uiBindings", [])}
        words: list[str] = []
        for handle in handles:
            if handle not in entity:
                continue
            hint = hints.get(normalize_candidate_target(f"fields.{entity[handle]}"))
            for word in hint["keywords"] if hint else ():
                if len(word) <= 100 and not any(c in word for c in "<>\r\n") and word not in words:
                    words.append(word)
        return words

    async def close(self, session_id: str | None = None) -> None:
        ids = [session_id] if session_id else list(self.sockets)
        pending = []
        for key in ids:
            socket = self.sockets.get(key)
            if socket:
                with suppress(RuntimeError):
                    await socket.close(code=1000)
            task = self.tasks.get(key)
            if task and task is not asyncio.current_task():
                task.cancel()
                pending.append(task)
            self.sockets.pop(key, None)
            self.tasks.pop(key, None)
            self.generations.pop(key, None)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.runtime.acquisition.clear(session_id)

    async def invalidate(self, session_id: str, scope: dict[str, Any]) -> None:
        generation = tuple(scope[k] for k in ("contextRevision", "interactionEpoch", "scopeDigest"))
        if session_id in self.generations and self.generations[session_id] != generation:
            await self.close(session_id)

    async def run(
        self, socket: WebSocket, session_id: str, factory: Any = upstream_connection
    ) -> None:
        accepted, token, connection_id, upstream_cm = False, "", None, None
        upstream: Any = None
        opened = time.monotonic()
        active: Utterance | None = None
        utterances: dict[str, Utterance] = {}
        commits: deque[tuple[str, str]] = deque()
        identities: dict[str, str] = {}
        holds: dict[str, list[str]] = {}
        final_wait: dict[str, float] = {}
        completed: set[str] = set()
        started_ids: set[str] = set()
        captions: dict[str, str] = {}
        keywords: list[str] = []
        question = ""
        lock = asyncio.Lock()
        upstream_lock = asyncio.Lock()
        renewal = asyncio.Event()
        renewal.set()

        async def send(value: dict[str, Any]) -> None:
            async with lock:
                await socket.send_json(value)

        async def open_upstream() -> None:
            nonlocal connection_id, upstream_cm, upstream, opened
            connection_id = await asyncio.to_thread(self.reserve, session_id, token)
            opened = time.monotonic()
            upstream_cm = factory(self.settings)
            upstream = await upstream_cm.__aenter__()
            context = await asyncio.to_thread(self.runtime.sessions.context, session_id, token)
            await upstream.send(
                json.dumps(
                    transcription_config(
                        await asyncio.to_thread(self.keywords, context, session_id, token)
                    )
                )
            )
            async with asyncio.timeout(10):
                while True:
                    event = json.loads(await upstream.recv())
                    if event.get("type") == "error":
                        raise AudioInvalid("Provider rejected transcription configuration")
                    if event.get("type") == "session.updated":
                        return

        async def close_upstream() -> None:
            nonlocal connection_id, upstream_cm, upstream
            if upstream_cm is not None:
                await upstream_cm.__aexit__(None, None, None)
                upstream_cm, upstream = None, None
            if connection_id:
                await asyncio.to_thread(self.finish, connection_id, time.monotonic() - opened)
                connection_id = None

        def forget(utterance: Utterance) -> None:
            for item_id in [
                key for key, uid in identities.items() if uid == utterance.utterance_id
            ]:
                identities.pop(item_id)
            utterances.pop(utterance.utterance_id, None)
            captions.pop(utterance.utterance_id, None)
            final_wait.pop(utterance.utterance_id, None)
            self.runtime.acquisition.release(utterance.utterance_id)

        async def cancel_held(utterance: Utterance, ids: list[str]) -> None:
            for command_id in ids:
                with suppress(Exception):
                    await self.runtime.controller.submit(
                        session_id,
                        token,
                        {
                            "contractVersion": "editing.v2",
                            "requestId": "drop_" + uuid4().hex,
                            "binding": utterance.binding,
                            "afterCommandId": None,
                            "command": {"kind": "cancel", "commandId": command_id},
                        },
                    )

        async def submit(utterance: Utterance) -> None:
            uid = utterance.utterance_id
            if uid in completed or utterance.text is None:
                return
            waiting = self.runtime.controller.pending.get(session_id)
            if not utterance.text or is_noise(
                utterance.text, answering=bool(waiting and waiting.question)
            ):
                # Noise or hesitation only. Held earlier instructions proceed.
                self.record(session_id, utteranceId=uid, text=utterance.text, outcome="ignored")
                completed.add(uid)
                ids = holds.pop(uid, [])
                if ids:
                    try:
                        await asyncio.to_thread(
                            self.runtime.repository.hold,
                            session_id,
                            token,
                            utterance.binding,
                            False,
                        )
                    except AppError:
                        await cancel_held(utterance, ids)
                forget(utterance)
                await send(
                    {
                        "type": "utterance.ignored",
                        "utteranceId": uid,
                        "message": "聞き取れる言葉がなかったため、何も変更していません。",
                    }
                )
                return
            try:
                await dispatch(utterance)
            except AppError as error:
                self.record(
                    session_id, utteranceId=uid, text=utterance.text, outcome=error.code,
                    message=error.detail,
                )
                # One rejected instruction never ends the voice connection. Instructions
                # still held for it are not released into a save.
                await cancel_held(utterance, holds.pop(uid, []))
                forget(utterance)
                await send(
                    {
                        "type": "command.rejected",
                        "utteranceId": uid,
                        "code": error.code,
                        "message": error.detail,
                    }
                )

        async def dispatch(utterance: Utterance) -> None:
            assert utterance.text
            completed.add(utterance.utterance_id)
            if len(completed) > 256:
                raise AudioInvalid("Connection instruction limit exceeded")
            final_wait.pop(utterance.utterance_id, None)
            pending = self.runtime.controller.pending.get(session_id)
            prior_ids = holds.get(utterance.utterance_id, [])
            await send(
                {
                    "type": "transcript.completed",
                    "utteranceId": utterance.utterance_id,
                    "text": utterance.text,
                    "transcriptDigest": utterance.transcript_digest,
                }
            )
            value: dict[str, Any] = {
                "contractVersion": "editing.v2",
                "requestId": "v_" + uuid4().hex,
                "binding": utterance.binding,
                "afterCommandId": prior_ids[-1] if prior_ids else None,
                "command": {
                    "kind": "instruction",
                    "input": {
                        "kind": "voice",
                        "utteranceId": utterance.utterance_id,
                        "transcriptDigest": utterance.transcript_digest,
                    },
                    "replyTo": {
                        "questionId": pending.question["questionId"],
                        "version": pending.question["version"],
                    }
                    if pending and pending.question
                    else None,
                },
            }
            scope_answer = bool(
                pending
                and pending.question
                and pending.question["missingKind"] == "scope"
                and pending.partial
                and pending.partial.get("scopeProposal") is not None
                and utterance.text.strip(" 。、!")
                in {"はい", "そうです", "切り替えて", "切り替え", "それでよろしく", "よろしく"}
            )
            if scope_answer and pending and pending.question and pending.partial:
                context = await asyncio.to_thread(self.runtime.sessions.context, session_id, token)
                scope = context["scopeContext"]
                result = await self.runtime.controller.rebind_scope(
                    session_id,
                    token,
                    {
                        "contractVersion": "editing.v2",
                        "requestId": value["requestId"],
                        "replyTo": value["command"]["replyTo"],
                        "context": {
                            "contractVersion": "editing.v2",
                            "kind": "context_update",
                            "expectedRevision": scope["resourceRevision"],
                            "expectedContextRevision": scope["contextRevision"],
                            "interactionEpoch": scope["interactionEpoch"],
                            "resource": scope["resource"],
                            "mode": scope["mode"],
                            "pageScope": scope["pageScope"],
                            "selectedTableId": pending.partial["scopeProposal"]["selectedTableId"],
                            "selectedHandles": scope["selectedHandles"],
                            "localTargetHandle": scope["localTargetHandle"],
                        },
                    },
                )
                context = await asyncio.to_thread(self.runtime.sessions.context, session_id, token)
                scope = context["scopeContext"]
                self.generations[session_id] = tuple(
                    scope[k] for k in ("contextRevision", "interactionEpoch", "scopeDigest")
                )
            elif prior_ids and self.policy.continues(utterance.text):
                if len(prior_ids) != 1:
                    raise reject(
                        "CONTINUATION_AMBIGUOUS", "複数の未保存指示の続きは特定できません。", 409
                    )
                value["afterCommandId"] = None
                result = await self.runtime.controller.continue_from(
                    session_id, token, value, prior_ids[0]
                )
            else:
                await asyncio.to_thread(
                    self.runtime.repository.hold, session_id, token, utterance.binding, False
                )
                holds.pop(utterance.utterance_id, None)
                result = await self.runtime.controller.submit(session_id, token, value)
            holds.pop(utterance.utterance_id, None)
            await send({"type": "command.accepted", "result": result})
            self.record(
                session_id,
                utteranceId=utterance.utterance_id,
                text=utterance.text,
                outcome="submitted",
                requestId=value["requestId"],
                commandId=result.get("commandId") if isinstance(result, dict) else None,
            )
            forget(utterance)

        try:
            if (
                (not getattr(socket.app.state, "cloud_authenticated", False)
                 and socket.url.hostname not in {"127.0.0.1", "localhost", "::1"})
                or socket.headers.get("origin") not in self.settings.app.frontend_origins
            ):
                await socket.close(code=1008)
                return
            await socket.accept()
            accepted = True
            raw = await asyncio.wait_for(socket.receive_text(), 5)
            auth = json.loads(raw) if len(raw) <= 200 else {}
            if (
                not isinstance(auth, dict)
                or set(auth) != {"type", "token"}
                or auth["type"] != "auth"
                or not isinstance(auth["token"], str)
                or not 32 <= len(auth["token"]) <= 100
            ):
                raise AudioInvalid("Invalid authentication envelope")
            token = auth["token"]
            await asyncio.to_thread(self.runtime.sessions.context, session_id, token)
            # A quick restart (e.g. after choosing a table) may arrive while the previous
            # connection is still shutting down; a live one is still never replaced.
            for _ in range(20):
                if session_id not in self.sockets:
                    break
                await asyncio.sleep(0.1)
            if session_id in self.sockets:
                raise reject(
                    "VOICE_ALREADY_CONNECTED",
                    "別の画面でこの書類の音声入力が動いています。そちらを止めてから押してください。",
                    409,
                )
            self.sockets[session_id] = socket
            task = asyncio.current_task()
            if task:
                self.tasks[session_id] = task
            await open_upstream()
            await send(
                {
                    "type": "ready",
                    "connectionSeconds": 300,
                    "utteranceSeconds": 180,
                    "silenceMs": self.policy.silence_ms,
                    "incompleteSilenceMs": self.policy.incomplete_ms,
                }
            )

            async def incoming() -> None:
                nonlocal active, keywords, question
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    async with upstream_lock:
                        if (data := message.get("bytes")) is not None:
                            if active is None:
                                raise AudioInvalid("PCM without an utterance")
                            active.append(data, time.monotonic())
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
                            raise AudioInvalid("Control is too large")
                        control = Control.validate_json(raw_control)
                        if isinstance(control, Begin):
                            if (
                                active is not None
                                or final_wait
                                or control.utterance_id in started_ids
                                or len(started_ids) >= 256
                            ):
                                raise AudioInvalid("An utterance is still incomplete")
                            started_ids.add(control.utterance_id)
                            # Renewal happens only at an idle boundary, before new speech.
                            if time.monotonic() - opened > 105:
                                renewal.clear()
                                await close_upstream()
                                await open_upstream()
                                renewal.set()
                                identities.clear()
                                await send({"type": "connection.renewed"})
                            context = await asyncio.to_thread(
                                self.runtime.sessions.context, session_id, token
                            )
                            bound = control.binding.model_dump(mode="json", by_alias=True)
                            if bound != binding(context):
                                raise reject(
                                    "VOICE_BINDING_STALE",
                                    "音声開始時の対象世代が一致しません。",
                                    409,
                                )
                            keywords = await asyncio.to_thread(
                                self.keywords, context, session_id, token
                            )
                            question = ""
                            waiting = self.runtime.controller.pending.get(session_id)
                            if waiting and waiting.question:
                                question = waiting.question["message"]
                                answers = [
                                    word
                                    for choice in waiting.question["choices"]
                                    for word in re.findall(r"\d+行目", choice["label"])
                                ]
                                keywords = list(dict.fromkeys([*answers, *keywords]))[:100]
                            await upstream.send(
                                json.dumps(
                                    transcription_config(keywords, question=question)
                                )
                            )
                            held = await asyncio.to_thread(
                                self.runtime.repository.hold, session_id, token, bound, True
                            )
                            active = self.runtime.acquisition.begin(
                                session_id, control.utterance_id, bound
                            )
                            utterances[active.utterance_id] = active
                            holds[active.utterance_id] = held
                            await send(
                                {
                                    "type": "potentialContinuation",
                                    "utteranceId": active.utterance_id,
                                    "heldCommandIds": held,
                                }
                            )
                        elif isinstance(control, SegmentBegin):
                            if active is None:
                                raise AudioInvalid("Segment without an utterance")
                            active.segment(control.segment_id, control.sequence)
                            if control.sequence == 1:
                                # One utterance is one instruction: reserve once, not per piece.
                                await asyncio.to_thread(
                                    self.budget.reserve, "editing.v2.voice.audio"
                                )
                            else:
                                preceding = captions.get(active.utterance_id, "") or "".join(
                                    s.text or "" for s in active.segments
                                )
                                if preceding.strip():
                                    await upstream.send(
                                        json.dumps(
                                            transcription_config(
                                                keywords, preceding, question=question
                                            )
                                        )
                                    )
                        elif isinstance(control, SegmentEnd):
                            if active is None:
                                raise AudioInvalid("Segment without an utterance")
                            active.commit(control.segment_id)
                            commits.append((active.utterance_id, control.segment_id))
                            await upstream.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        elif isinstance(control, End):
                            if active is None:
                                raise AudioInvalid("End without an utterance")
                            context = await asyncio.to_thread(
                                self.runtime.sessions.context, session_id, token
                            )
                            local = context["scopeContext"]["mode"] == "local"
                            if (control.reason == "local_stop") != local:
                                raise AudioInvalid("Invalid boundary mode")
                            caption = captions.get(active.utterance_id, "") or "".join(
                                s.text or "" for s in active.segments
                            )
                            if not local and control.silence_ms < self.policy.wait_ms(caption):
                                raise AudioInvalid("Silence boundary is premature")
                            active.end(control.manifest, time.monotonic())
                            final_wait[active.utterance_id] = time.monotonic() + 15
                            await send(
                                {"type": "processing", "message": "音声を受信・指示を整理中"}
                            )
                            await submit(active)
                            active = None
                        elif control.type == "speech.stopped":
                            await send(
                                {"type": "processing", "message": "音声を受信・指示を整理中"}
                            )
                        elif control.type == "utterance.cancel":
                            if active:
                                active.clear()
                                await upstream.send(
                                    json.dumps({"type": "input_audio_buffer.clear"})
                                )
                                await asyncio.to_thread(
                                    self.runtime.repository.hold,
                                    session_id,
                                    token,
                                    active.binding,
                                    False,
                                )
                                holds.pop(active.utterance_id, None)
                                self.runtime.acquisition.release(active.utterance_id)
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
                        # Only intentional, already successful idle renewal may replace the socket.
                        if not renewal.is_set():
                            await renewal.wait()
                        if source is not upstream and upstream is not None:
                            continue
                        raise
                    event = json.loads(raw)
                    kind, item_id = event.get("type"), event.get("item_id")
                    if kind == "input_audio_buffer.committed":
                        if not commits or not isinstance(item_id, str) or item_id in identities:
                            raise AudioInvalid("Invalid commit order")
                        uid, sid = commits.popleft()
                        utterances[uid].committed(sid, item_id)
                        identities[item_id] = uid
                    elif kind == "conversation.item.input_audio_transcription.completed":
                        if item_id not in identities or not isinstance(
                            event.get("transcript"), str
                        ):
                            raise AudioInvalid("Unowned transcript")
                        utterance = utterances[identities[item_id]]
                        if utterance.failed:
                            identities.pop(item_id)
                            continue
                        utterance.completed(item_id, event["transcript"])
                        await submit(utterance)
                    elif kind == "conversation.item.input_audio_transcription.delta":
                        delta = event.get("delta")
                        delta_uid = identities.get(item_id) or (
                            active.utterance_id if active else None
                        )
                        if (
                            isinstance(delta, str)
                            and len(delta) <= 20000
                            and delta_uid
                            and not utterances[delta_uid].failed
                        ):
                            captions[delta_uid] = captions.get(delta_uid, "") + delta
                            if len(captions[delta_uid]) > 20000:
                                raise AudioInvalid("Live caption limit exceeded")
                            await send(
                                {
                                    "type": "transcript.delta",
                                    "utteranceId": delta_uid,
                                    "delta": delta,
                                }
                            )
                    elif kind in {"error", "conversation.item.input_audio_transcription.failed"}:
                        raise AudioInvalid("Transcription failed")

            async def watchdog() -> None:
                while True:
                    await asyncio.sleep(0.25)
                    self.runtime.acquisition.prune()
                    now = time.monotonic()
                    if now - opened >= 300 and active is None and not final_wait:
                        raise reject(
                            "VOICE_IDLE",
                            "しばらく話がなかったため音声入力を止めました。"
                            "続けるには「音声入力」を押してください。",
                            409,
                        )
                    if (
                        now - opened >= 300
                        or (active and now - active.started > 180)
                        or any(v <= now for v in final_wait.values())
                    ):
                        raise reject(
                            "VOICE_DEADLINE", "音声の取得又は認識が制限時間を超えました。", 409
                        )
                    await asyncio.to_thread(self.runtime.sessions.owner_probe, session_id, token)

            tasks = [asyncio.create_task(coro()) for coro in (incoming, outgoing, watchdog)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
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
                            else "音声取得を停止しました。未完了の指示は保存していません。",
                        }
                    )
        finally:
            # Release the registration first: a cancelled shutdown must never leave the
            # session looking connected (which refused every later voice start).
            registered = self.sockets.get(session_id) is socket
            if registered:
                self.sockets.pop(session_id, None)
                self.tasks.pop(session_id, None)
                self.generations.pop(session_id, None)
            with suppress(Exception, asyncio.CancelledError):
                await close_upstream()
            # Recognition failure cannot release a speculative plan into a save.
            for uid, ids in holds.items():
                for command_id in ids:
                    if token and uid in utterances:
                        with suppress(Exception):
                            await self.runtime.controller.submit(
                                session_id,
                                token,
                                {
                                    "contractVersion": "editing.v2",
                                    "requestId": "stop_" + uuid4().hex,
                                    "binding": utterances[uid].binding,
                                    "afterCommandId": None,
                                    "command": {"kind": "cancel", "commandId": command_id},
                                },
                            )
            if registered:
                self.runtime.acquisition.clear(session_id)
            if accepted:
                with suppress(RuntimeError, WebSocketDisconnect, OSError):
                    await socket.close(code=1000)
