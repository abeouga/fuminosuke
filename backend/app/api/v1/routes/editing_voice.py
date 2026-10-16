from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket
from sqlalchemy import inspect

from app.ai.api_budget import ApiBudget
from app.api.dependencies import get_app_settings
from app.api.v1.routes.editing import (
    EditingRoute,
    Token,
    controller,
    enabled,
    local_request,
    service,
)
from app.assistant.audio import normalize_audio, normalize_speech
from app.assistant.conversation import context_binding
from app.assistant.conversation_contracts import ResolveTurn, TurnResolution
from app.assistant.stream_contracts import SpeechRequest, UiTargets
from app.assistant.stream_proxy import StreamRegistry, run_proxy, upstream_connection
from app.assistant.stream_service import StreamService
from app.assistant.voice_provider import VoiceProvider
from app.editing.errors import reject


class VoiceRoute(EditingRoute):
    def body_limit(self, request: Request) -> int:
        return 10_485_760 if request.url.path.endswith("/audio") else super().body_limit(request)


router = APIRouter(prefix="/editing", tags=["editing-voice"], route_class=VoiceRoute)


def voice_enabled(request: Request) -> None:
    enabled(request)
    if not {"editing_voice_streams", "editing_voice_audio_items"} <= set(
        inspect(service(request).database.engine).get_table_names()
    ):
        raise reject("EDITING_SCHEMA_REQUIRED", "音声接続のDB移行が必要です。", 503)


http_guard = [Depends(local_request), Depends(voice_enabled)]


def registry(request: Request) -> StreamRegistry:
    if not hasattr(request.app.state, "editing_streams"):
        request.app.state.editing_streams = StreamRegistry()
    return cast(StreamRegistry, request.app.state.editing_streams)


@router.get("/voice/status", dependencies=http_guard)
def status(request: Request) -> dict[str, Any]:
    settings = get_app_settings(request)
    return {
        **StreamService(service(request)).status(),
        "configured": settings.voice.provider == "openai" and bool(settings.ocr.openai_api_key),
        "localAvailable": bool(settings.voice.ffmpeg_path),
    }


@router.post("/sessions/{session_id}/voice/streams", dependencies=http_guard)
def create_stream(request: Request, session_id: str, token: Token) -> dict[str, Any]:
    settings = get_app_settings(request)
    if settings.voice.provider != "openai" or not settings.ocr.openai_api_key:
        raise reject("VOICE_NOT_CONFIGURED", "音声APIが設定されていません。", 503)
    return StreamService(service(request)).create(session_id, token)


@router.websocket("/sessions/{session_id}/voice/streams/{stream_id}")
async def stream(socket: WebSocket, session_id: str, stream_id: str) -> None:
    request = cast(Request, socket)
    try:
        voice_enabled(request)
    except Exception:
        await socket.close(code=1008)
        return
    await run_proxy(
        socket,
        get_app_settings(request),
        StreamService(service(request)),
        registry(request),
        session_id,
        stream_id,
        getattr(socket.app.state, "editing_upstream_factory", upstream_connection),
    )


@router.post("/sessions/{session_id}/ui-targets", dependencies=http_guard)
def ui_targets(request: Request, session_id: str, token: Token, body: UiTargets) -> dict[str, Any]:
    editing = service(request)
    with editing.database.session() as session:
        owner = editing._owner(session, session_id, token)
        catalog = editing.catalogs.build(session, owner)
        if (
            body.context_revision != owner.context_revision
            or body.resource_revision != catalog.revision
        ):
            raise reject("CONTEXT_STALE", "画面の選択情報が古くなっています。")
        targets = [
            target
            for target in catalog.targets.values()
            if (
                target.kind == ("cell" if owner.resource_kind == "template" else "field")
                and target.entity_id in body.entity_ids
            )
            or target.handle in body.handles
        ]
        if set(body.entity_ids) - {target.entity_id for target in targets} or set(body.handles) - {
            target.handle for target in targets
        }:
            raise reject("TARGET_STALE", "選択対象が現在のページに属していません。")
        if owner.resource_kind == "template" and body.handles:
            # Highlight the physical owners of a meaning/topology edit using
            # only the current, page-scoped catalog. No coordinates are inferred.
            pending = list(targets)
            projected = {target.handle: target for target in targets}
            while pending:
                target = pending.pop()
                handles = [
                    target.detail.get("ownerHandle"),
                    *target.detail.get("memberHandles", []),
                ]
                related = [catalog.targets[h] for h in handles if h in catalog.targets]
                if target.kind in {"meaning", "record_section"}:
                    related.extend(
                        other
                        for other in catalog.targets.values()
                        if other.detail.get("meaningHandle") == target.handle
                        or other.detail.get("ownerHandle") == target.handle
                    )
                for other in related:
                    if other.handle not in projected:
                        projected[other.handle] = other
                        pending.append(other)
            targets = list(projected.values())
        # This projection is only for physical human UI selection/highlighting.
        # Function tools and model context keep opaque catalog handles.
        return {
            "targets": [
                {
                    "handle": target.handle,
                    "kind": target.kind,
                    "entityId": target.entity_id,
                    "pageId": target.page_id,
                }
                for target in targets
            ]
        }


@router.post(
    "/sessions/{session_id}/conversation/turns/{turn_id}/audio",
    dependencies=http_guard,
    response_model=TurnResolution,
    response_model_exclude_unset=True,
)
async def local_audio(
    request: Request,
    session_id: str,
    turn_id: str,
    token: Token,
    reply_to: str | None = Query(default=None, max_length=36),
) -> TurnResolution:
    editing = service(request)
    with editing.database.session() as session:
        owner = editing._owner(session, session_id, token)
        if owner.mode != "local":
            raise reject("LOCAL_SCOPE_MISMATCH", "局所録音session専用です。")
    editing.turn_context(session_id, token, turn_id)
    settings = get_app_settings(request)
    wav = await asyncio.to_thread(
        normalize_audio,
        await request.body(),
        request.headers.get("content-type", "").split(";")[0],
        settings.voice.model_copy(
            update={"max_record_seconds": min(30, settings.voice.max_record_seconds)}
        ),
    )
    text = await VoiceProvider(settings, ApiBudget(editing.database)).transcribe(wav)
    editing.turn_context(session_id, token, turn_id)
    return await controller(request).resolve(
        session_id, token, turn_id, ResolveTurn(text=text, reply_to=reply_to)
    )


@router.post("/sessions/{session_id}/voice/speech", dependencies=http_guard)
async def speech(request: Request, session_id: str, token: Token, body: SpeechRequest) -> Response:
    editing = service(request)
    if bool(body.request_id) == bool(body.turn_id):
        raise reject("INVALID_ARGUMENT", "保存結果または質問を一つ指定してください。", 422)
    if body.request_id:
        receipt = editing.receipt(session_id, token, body.request_id)
        if receipt["state"] != "SUCCEEDED" or not receipt["result"]:
            raise reject("VOICE_NOTIFICATION_PENDING", "保存結果が確定していません。")
        text = receipt["result"]["notification"]
    else:
        conversation = controller(request)
        memory = conversation.turns.get(body.turn_id or "")
        if (
            memory is None
            or memory.session_id != session_id
            or memory.expires <= time.monotonic()
            or memory.result is None
            or memory.result.state != "CLARIFYING"
        ):
            raise reject("VOICE_QUESTION_EXPIRED", "質問が失効しました。")
        with editing.database.session() as session:
            owner = editing._owner(session, session_id, token)
            catalog = editing.catalogs.build(session, owner)
            if context_binding(editing._context(session, owner, catalog)) != memory.binding:
                raise reject("CONTEXT_STALE", "質問後に編集状態が変わりました。")
        text = memory.result.message
    data = await VoiceProvider(get_app_settings(request), ApiBudget(editing.database)).speech(text)
    normalized, _ = normalize_speech(data)
    return Response(normalized, media_type="audio/wav", headers={"Cache-Control": "no-store"})
