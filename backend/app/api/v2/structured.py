from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request, WebSocket

from app.ai.api_budget import ApiBudget
from app.api.dependencies import get_app_settings, get_database
from app.api.editing_transport import EditingRoute, local_request
from app.editing.errors import reject
from app.structured.contracts import (
    DocumentList,
    FieldNameList,
    QuestionRequest,
    QuestionResult,
    StructuredDocument,
    StructuredStatus,
)
from app.structured.decisions import OpenAIDecisions
from app.structured.qa import QuestionEngine
from app.structured.service import StructuredDocumentService
from app.structured.voice import QuestionVoice


def enabled(request: Request) -> None:
    if not get_app_settings(request).app.structured_data_enabled:
        raise reject("STRUCTURED_DATA_DISABLED", "構造化データAPIは無効です。", 503)


def runtime(request: Request) -> QuestionEngine:
    if not hasattr(request.app.state, "structured_data"):
        settings = get_app_settings(request)
        database = get_database(request)
        chooser = OpenAIDecisions(settings, ApiBudget(database))
        request.app.state.structured_data = QuestionEngine(
            StructuredDocumentService(database), chooser if chooser.configured else None
        )
    return cast(QuestionEngine, request.app.state.structured_data)


router = APIRouter(
    prefix="/api/v2/structured",
    tags=["structured.v1"],
    route_class=EditingRoute,
    dependencies=[Depends(local_request)],
)


@router.get("/status", response_model=StructuredStatus, response_model_by_alias=True)
async def status(request: Request) -> dict[str, Any]:
    settings = get_app_settings(request)
    connected = bool(
        settings.app.structured_data_enabled
        and settings.voice.provider == "openai"
        and settings.ocr.openai_api_key
    )
    return {
        "enabled": settings.app.structured_data_enabled,
        "qaConnected": connected,
        "voiceConnected": connected,
    }


@router.get(
    "/documents",
    dependencies=[Depends(enabled)],
    response_model=DocumentList,
    response_model_by_alias=True,
)
async def list_documents(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    template_id: str | None = Query(default=None, alias="templateId", max_length=100),
    q: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    items = await asyncio.to_thread(
        runtime(request).service.list, limit=limit, template_id=template_id, query=q
    )
    return {"items": items}


@router.get(
    "/documents/{job_id}",
    dependencies=[Depends(enabled)],
    response_model=StructuredDocument,
    response_model_by_alias=True,
)
async def get_document(request: Request, job_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(runtime(request).service.get, job_id)


@router.get(
    "/fields",
    dependencies=[Depends(enabled)],
    response_model=FieldNameList,
    response_model_by_alias=True,
)
async def list_fields(
    request: Request,
    template_id: str = Query(alias="templateId", min_length=1, max_length=100),
) -> dict[str, Any]:
    items = await asyncio.to_thread(runtime(request).service.field_names, template_id)
    return {"items": items}


@router.post(
    "/questions",
    dependencies=[Depends(enabled)],
    response_model=QuestionResult,
    response_model_by_alias=True,
)
async def ask(request: Request, body: QuestionRequest) -> dict[str, Any]:
    reply = (
        {"questionId": body.reply_to.question_id, "value": body.reply_to.value}
        if body.reply_to is not None
        else None
    )
    return await runtime(request).ask(body.text, reply, body.document_ids)


voice_router = APIRouter(prefix="/api/v2/structured", tags=["structured.v1.voice"])


@voice_router.websocket("/voice")
async def voice(socket: WebSocket) -> None:
    """Transcription only; the browser sends the text to /questions like typed input."""
    request = cast(Request, socket)
    try:
        enabled(request)
        settings = get_app_settings(request)
        service = QuestionVoice(
            settings, ApiBudget(get_database(request)), runtime(request).service
        )
        if not service.configured:
            raise reject("VOICE_NOT_CONFIGURED", "音声取得の設定がありません。", 503)
    except Exception:
        await socket.close(code=1008)
        return
    from app.ai.realtime_transcription import upstream_connection

    await service.run(
        socket, getattr(socket.app.state, "structured_voice_upstream_factory", upstream_connection)
    )
