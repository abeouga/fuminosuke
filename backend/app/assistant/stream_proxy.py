"""Authenticated, bounded transcription proxy; no upstream event is a mutation."""

from __future__ import annotations

import asyncio
import base64
import json
from collections import deque
from contextlib import suppress
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from app.ai.realtime_transcription import (
    SESSION_CONFIG as SESSION_CONFIG,
)
from app.ai.realtime_transcription import (
    UPSTREAM_URL as UPSTREAM_URL,
)
from app.ai.realtime_transcription import (
    transcription_config as transcription_config,
)
from app.ai.realtime_transcription import (
    upstream_connection as upstream_connection,
)
from app.assistant.stream_contracts import AudioBegin, AudioControl
from app.assistant.stream_service import StreamService
from app.core.config import Settings, local_host
from app.core.errors import AppError
from app.editing.errors import reject


class StreamRegistry:
    def __init__(self) -> None:
        self.sockets: dict[str, set[WebSocket]] = {}
        self.tasks: dict[WebSocket, asyncio.Task[Any]] = {}

    async def close(self, session_id: str | None = None) -> None:
        sockets = (
            list(self.sockets.get(session_id, set()))
            if session_id
            else [socket for group in self.sockets.values() for socket in group]
        )
        await asyncio.gather(
            *(socket.close(code=1000) for socket in sockets), return_exceptions=True
        )
        tasks = [
            self.tasks[socket]
            for socket in sockets
            if socket in self.tasks and self.tasks[socket] is not asyncio.current_task()
        ]
        if tasks:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def run_proxy(
    socket: WebSocket,
    settings: Settings,
    service: StreamService,
    registry: StreamRegistry,
    session_id: str,
    stream_id: str,
    factory: Any = upstream_connection,
) -> None:
    accepted = False
    registered = False
    active: str | None = None
    commits: deque[str] = deque()
    identities: dict[str, str] = {}
    deferred_deltas: dict[str, str] = {}
    completed: set[str] = set()
    send_lock = asyncio.Lock()

    async def send(data: dict[str, Any]) -> None:
        async with send_lock:
            await socket.send_json(data)

    try:
        if (
            (not getattr(socket.app.state, "cloud_authenticated", False)
             and not local_host(socket.url.hostname))
            or socket.headers.get("origin") not in settings.app.frontend_origins
        ):
            await socket.close(code=1008)
            return
        await socket.accept()
        accepted = True
        auth = await asyncio.wait_for(socket.receive_text(), timeout=5)
        if (
            len(auth) > 200
            or not isinstance(value := json.loads(auth), dict)
            or set(value) != {"type", "token"}
            or value["type"] != "auth"
            or not isinstance(value["token"], str)
            or not 32 <= len(value["token"]) <= 100
        ):
            raise reject("EDITING_SESSION_INVALID", "音声接続の認証形式が不正です。", 403)
        token = value["token"]
        # Configuration is checked before consuming the irreversible WS attempt.
        if settings.voice.provider != "openai" or not settings.ocr.openai_api_key:
            raise reject("VOICE_NOT_CONFIGURED", "音声APIが設定されていません。", 503)
        seconds = await asyncio.to_thread(service.connect, session_id, token, stream_id)
        registry.sockets.setdefault(session_id, set()).add(socket)
        if (task := asyncio.current_task()) is not None:
            registry.tasks[socket] = task
        registered = True
        async with asyncio.timeout(seconds), factory(settings) as upstream:
            keywords = service.transcription_keywords(session_id, token, stream_id)
            await upstream.send(json.dumps(transcription_config(keywords)))
            async with asyncio.timeout(10):
                while True:
                    event = json.loads(await upstream.recv())
                    if event.get("type") == "error":
                        raise reject(
                            "VOICE_UPSTREAM_REJECTED", "音声接続設定が拒否されました。", 502
                        )
                    if event.get("type") == "session.updated":
                        break
            await send({"type": "ready", "seconds": seconds})

            async def incoming() -> None:
                nonlocal active, keywords
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if (data := message.get("bytes")) is not None:
                        if active is None:
                            raise reject(
                                "VOICE_AUDIO_UNKNOWN", "録音開始前の音声は送信できません。"
                            )
                        await asyncio.to_thread(
                            service.append, session_id, token, stream_id, active, data,
                        )
                        await upstream.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(data).decode("ascii"),
                                }
                            )
                        )
                        continue
                    raw = message.get("text", "")
                    if len(raw) > 4096:
                        raise reject("VOICE_CONTROL_TOO_LARGE", "音声制御要求が長すぎます。", 413)
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid control")
                    if payload.get("type") == "audio.begin":
                        body = AudioBegin.model_validate(payload)
                        if active is not None:
                            raise reject("VOICE_AUDIO_PENDING", "前の録音が終了していません。")
                        current_keywords = service.transcription_keywords(
                            session_id, token, stream_id
                        )
                        if current_keywords != keywords:
                            await upstream.send(json.dumps(transcription_config(current_keywords)))
                            keywords = current_keywords
                        await send(service.begin(session_id, token, stream_id, body.audio_id))
                        active = body.audio_id
                    else:
                        control = AudioControl.model_validate(payload)
                        if (
                            control.type in {"audio.end", "audio.cancel"}
                            and active != control.audio_id
                        ):
                            raise reject("VOICE_AUDIO_UNKNOWN", "録音中の発話IDと一致しません。")
                        service.control(
                            session_id, token, stream_id, control.audio_id, control.type
                        )
                        if control.type == "audio.end":
                            commits.append(control.audio_id)
                            await upstream.send(json.dumps({"type": "input_audio_buffer.commit"}))
                            active = None
                        elif control.type == "audio.cancel":
                            await upstream.send(json.dumps({"type": "input_audio_buffer.clear"}))
                            active = None
                            deferred_deltas.clear()
                        if control.type == "audio.done":
                            completed.discard(control.audio_id)
                            for key in [
                                key
                                for key, audio in identities.items()
                                if audio == control.audio_id
                            ]:
                                identities.pop(key)
                        await send(
                            {
                                "type": "audio.ack",
                                "action": control.type,
                                "audioId": control.audio_id,
                            }
                        )

            async def outgoing() -> None:
                async for raw in upstream:
                    event = json.loads(raw)
                    kind = event.get("type")
                    item_id = event.get("item_id")
                    if kind == "input_audio_buffer.committed":
                        if (
                            not commits
                            or not isinstance(item_id, str)
                            or len(item_id) > 100
                            or item_id in identities
                        ):
                            raise reject("VOICE_ORDER_INVALID", "音声commitの順序が不正です。", 502)
                        audio_id = commits.popleft()
                        identities[item_id] = audio_id
                        service.committed(stream_id, audio_id, item_id)
                        if caption := deferred_deltas.pop(item_id, None):
                            await send(
                                {"type": "transcript.delta", "audioId": audio_id, "delta": caption}
                            )
                    elif kind == "conversation.item.input_audio_transcription.delta":
                        delta_audio_id = identities.get(item_id)
                        delta = event.get("delta")
                        if (
                            delta_audio_id is None
                            and isinstance(item_id, str)
                            and isinstance(delta, str)
                        ):
                            if item_id not in deferred_deltas and len(deferred_deltas) >= 2:
                                raise reject(
                                    "VOICE_ORDER_INVALID", "未対応の音声字幕が多すぎます。", 502
                                )
                            caption = deferred_deltas.get(item_id, "") + delta
                            if len(caption) > 2000:
                                raise reject(
                                    "VOICE_TRANSCRIPT_INVALID", "音声字幕が長すぎます。", 422
                                )
                            deferred_deltas[item_id] = caption
                        if (
                            delta_audio_id
                            and delta_audio_id not in completed
                            and isinstance(delta, str)
                            and len(delta) <= 2000
                        ):
                            await send(
                                {
                                    "type": "transcript.delta",
                                    "audioId": delta_audio_id,
                                    "delta": delta,
                                }
                            )
                    elif kind == "conversation.item.input_audio_transcription.completed":
                        text = event.get("transcript")
                        if (
                            not isinstance(item_id, str)
                            or not isinstance(text, str)
                            or not 1 <= len(text.strip()) <= 2000
                        ):
                            raise reject(
                                "VOICE_TRANSCRIPT_INVALID",
                                "認識結果が空か長すぎます。再指示してください。",
                                422,
                            )
                        result = service.completed(stream_id, item_id)
                        if result:
                            completed.add(result["audioId"])
                            await send(
                                {"type": "transcript.completed", **result, "text": text.strip()}
                            )
                    elif kind in {"error", "conversation.item.input_audio_transcription.failed"}:
                        raise reject(
                            "VOICE_UPSTREAM_FAILED",
                            "音声認識を完了できませんでした。再指示してください。",
                            502,
                        )

            async def watchdog() -> None:
                while True:
                    await asyncio.sleep(1)
                    service.check_deadlines(session_id, token, stream_id)

            tasks = [
                asyncio.create_task(incoming()),
                asyncio.create_task(outgoing()),
                asyncio.create_task(watchdog()),
            ]
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
        # Registry.close closes the browser socket before cancelling its task.
        # This is a normal session stop; unrelated cancellation still propagates.
        if socket.application_state != WebSocketState.DISCONNECTED:
            raise
    except (AppError, TimeoutError, ValidationError, ValueError, OSError) as error:
        code = error.code if isinstance(error, AppError) else "VOICE_STREAM_FAILED"
        message = (
            error.detail
            if isinstance(error, AppError)
            else "音声接続が終了しました。取得を停止して再指示してください。"
        )
        if accepted:
            with suppress(RuntimeError, WebSocketDisconnect, OSError):
                await send({"type": "error", "code": code, "message": message})
    except Exception:
        # Never relay an upstream error/body/token/audio into application logs.
        if accepted:
            with suppress(RuntimeError, WebSocketDisconnect, OSError):
                await send(
                    {
                        "type": "error",
                        "code": "VOICE_STREAM_FAILED",
                        "message": "音声接続を完了できませんでした。",
                    }
                )
    finally:
        if registered:
            registry.tasks.pop(socket, None)
            registry.sockets.get(session_id, set()).discard(socket)
            if not registry.sockets.get(session_id):
                registry.sockets.pop(session_id, None)
            service.close(stream_id)
        if accepted:
            with suppress(RuntimeError, WebSocketDisconnect, OSError):
                await socket.close(code=1000)
