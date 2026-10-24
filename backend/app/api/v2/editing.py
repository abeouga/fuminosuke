from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy import inspect

from app.api.dependencies import get_app_settings, get_database, get_storage
from app.api.editing_transport import EditingRoute, Token, local_request
from app.api.v2.editing_contracts import (
    CloseResult,
    CommandRequest,
    CommandResult,
    ContextResult,
    ContextUpdate,
    ManualAuthoring,
    ManualOcr,
    ScopeAnswer,
    SessionCreate,
    SessionResult,
)
from app.editing.errors import reject
from app.editing.infrastructure.runtime import EditingRuntime


class V2Route(EditingRoute):
    def body_limit(self, request: Request) -> int:
        return (
            2_097_152
            if request.url.path.endswith(("/manual-authoring", "/manual-ocr"))
            else 262_144
        )


def enabled(request: Request) -> None:
    if not get_app_settings(request).app.editing_api_enabled:
        raise reject("EDITING_API_DISABLED", "新しい編集APIは試験用環境だけで有効です。", 503)
    if "editing_command_requests" not in inspect(get_database(request).engine).get_table_names():
        raise reject("EDITING_SCHEMA_REQUIRED", "編集機能の準備ができていません。", 503)
    if "held" not in {
        c["name"]
        for c in inspect(get_database(request).engine).get_columns("editing_command_requests")
    }:
        raise reject("EDITING_SCHEMA_REQUIRED", "音声継続のDB移行が必要です。", 503)


def runtime(request: Request) -> EditingRuntime:
    if not hasattr(request.app.state, "editing_v2"):
        request.app.state.editing_v2 = EditingRuntime(
            get_database(request), get_storage(request), settings=get_app_settings(request)
        )
    return request.app.state.editing_v2  # type: ignore[no-any-return]


router = APIRouter(
    prefix="/api/v2/editing",
    tags=["editing.v2"],
    route_class=V2Route,
    dependencies=[Depends(local_request)],
)


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return {
        "contractVersion": "editing.v2",
        "enabled": get_app_settings(request).app.editing_api_enabled,
        "templateAnalysisEnabled": get_app_settings(request).app.template_analysis_enabled,
        "plannerConnected": bool(
            get_app_settings(request).app.editing_api_enabled
            and get_app_settings(request).voice.provider == "openai"
            and get_app_settings(request).ocr.openai_api_key
        ),
        "voiceConnected": bool(
            get_app_settings(request).app.editing_api_enabled
            and get_app_settings(request).voice.provider == "openai"
            and get_app_settings(request).ocr.openai_api_key
        ),
        "voiceSecondsRemaining": await asyncio.to_thread(voice_seconds_remaining, request),
    }


def voice_seconds_remaining(request: Request) -> int | None:
    database = get_database(request)
    if not get_app_settings(request).ocr.limits_enabled:
        return None  # no daily voice time while AI limits are off
    if "editing_voice_streams" not in inspect(database.engine).get_table_names():
        return None
    from app.editing.infrastructure.voice_stream import used_today

    with database.session() as session:
        return max(0, get_app_settings(request).voice.daily_seconds - used_today(session))


@router.post(
    "/sessions",
    dependencies=[Depends(enabled)],
    response_model=SessionResult,
    response_model_exclude_unset=True,
)
async def create(request: Request, body: SessionCreate) -> dict[str, Any]:
    return await asyncio.to_thread(
        runtime(request).sessions.create, body.model_dump(mode="json", by_alias=True)
    )


@router.get(
    "/sessions/{session_id}/context",
    dependencies=[Depends(enabled)],
    response_model=ContextResult,
    response_model_exclude_unset=True,
)
async def context(request: Request, session_id: str, token: Token) -> dict[str, Any]:
    await asyncio.to_thread(runtime(request).repository.expire_questions, session_id, token)
    result = await asyncio.to_thread(runtime(request).sessions.context, session_id, token)
    runtime(request).controller.abort_resolution(
        session_id, if_context_revision=result["scopeContext"]["contextRevision"]
    )
    connections = runtime(request).voice
    if connections:
        await connections.invalidate(session_id, result["scopeContext"])
    return result


@router.put(
    "/sessions/{session_id}/context",
    dependencies=[Depends(enabled)],
    response_model=ContextResult,
    response_model_exclude_unset=True,
)
async def update(
    request: Request, session_id: str, token: Token, body: ContextUpdate
) -> dict[str, Any]:
    result = await asyncio.to_thread(
        runtime(request).sessions.update,
        session_id,
        token,
        body.model_dump(mode="json", by_alias=True),
    )
    runtime(request).controller.abort_resolution(
        session_id, if_context_revision=result["scopeContext"]["contextRevision"]
    )
    connections = runtime(request).voice
    if connections:
        await connections.invalidate(session_id, result["scopeContext"])
    return result


@router.delete(
    "/sessions/{session_id}", dependencies=[Depends(enabled)], response_model=CloseResult
)
async def close(request: Request, session_id: str, token: Token) -> dict[str, str]:
    result = await asyncio.to_thread(runtime(request).sessions.close, session_id, token)
    runtime(request).controller.abort_resolution(session_id)
    connections = runtime(request).voice
    if connections:
        await connections.close(session_id)
    runtime(request).acquisition.clear(session_id)
    return result


@router.post(
    "/sessions/{session_id}/commands",
    status_code=202,
    response_model=CommandResult,
    dependencies=[Depends(enabled)],
)
async def command(
    request: Request, session_id: str, token: Token, body: CommandRequest
) -> dict[str, Any]:
    return await runtime(request).controller.submit(
        session_id, token, body.model_dump(mode="json", by_alias=True)
    )


@router.post(
    "/sessions/{session_id}/manual-authoring",
    status_code=202,
    response_model=CommandResult,
    dependencies=[Depends(enabled)],
)
async def authoring(
    request: Request, session_id: str, token: Token, body: ManualAuthoring
) -> dict[str, Any]:
    return await runtime(request).controller.submit(
        session_id, token, body.model_dump(mode="json", by_alias=True)
    )


@router.get(
    "/sessions/{session_id}/commands/{request_id}",
    dependencies=[Depends(enabled)],
    response_model=CommandResult,
)
async def query(request: Request, session_id: str, request_id: str, token: Token) -> dict[str, Any]:
    await asyncio.to_thread(runtime(request).repository.expire_questions, session_id, token)
    return await asyncio.to_thread(runtime(request).repository.query, session_id, token, request_id)


@router.post(
    "/sessions/{session_id}/manual-ocr",
    status_code=202,
    response_model=CommandResult,
    dependencies=[Depends(enabled)],
)
async def manual_ocr(
    request: Request, session_id: str, token: Token, body: ManualOcr
) -> dict[str, Any]:
    return await runtime(request).controller.submit(
        session_id, token, body.model_dump(mode="json", by_alias=True)
    )


@router.post(
    "/sessions/{session_id}/scope-answer",
    status_code=202,
    dependencies=[Depends(enabled)],
    response_model=CommandResult,
)
async def scope_answer(
    request: Request, session_id: str, token: Token, body: ScopeAnswer
) -> dict[str, Any]:
    result = await runtime(request).controller.rebind_scope(
        session_id, token, body.model_dump(mode="json", by_alias=True)
    )
    connections = runtime(request).voice
    if connections:
        await connections.close(session_id)
    return result


@router.get("/sessions/{session_id}/events", dependencies=[Depends(enabled)])
async def events(
    request: Request,
    session_id: str,
    token: Token,
    last_event_id: Annotated[str | None, Header(max_length=128)] = None,
) -> StreamingResponse:
    repository = runtime(request).repository
    first = await asyncio.to_thread(repository.replay, session_id, token, last_event_id)

    async def stream() -> AsyncIterator[str]:
        cursor, batch, heartbeat = last_event_id, first, time.monotonic()
        while not await request.is_disconnected():
            for event in batch:
                cursor = event["eventId"]
                yield (
                    f"id: {cursor}\nevent: {event['payload']['type']}\n"
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )
            if time.monotonic() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(0.25)
            batch = await asyncio.to_thread(repository.replay, session_id, token, cursor)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


voice_router = APIRouter(prefix="/api/v2/editing", tags=["editing.v2.voice"])


@voice_router.websocket("/sessions/{session_id}/voice")
async def voice(socket: WebSocket, session_id: str) -> None:
    request = cast(Request, socket)
    try:
        enabled(request)
        if "held" not in {
            c["name"]
            for c in inspect(get_database(request).engine).get_columns("editing_command_requests")
        }:
            raise reject("EDITING_SCHEMA_REQUIRED", "音声継続のDB移行が必要です。", 503)
        connections = runtime(request).voice
        if connections is None:
            raise reject("VOICE_NOT_CONFIGURED", "音声取得の設定がありません。", 503)
    except Exception:
        await socket.close(code=1008)
        return
    from app.ai.realtime_transcription import upstream_connection

    await connections.run(
        socket,
        session_id,
        getattr(socket.app.state, "editing_v2_upstream_factory", upstream_connection),
    )
