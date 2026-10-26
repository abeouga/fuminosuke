from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket

from app.ai.api_budget import ApiBudget
from app.api.dependencies import get_app_settings, get_database
from app.api.editing_transport import EditingRoute, local_request
from app.core.errors import AppError
from app.editing.errors import reject
from app.structured.contracts import (
    ChatEvent,
    DocumentList,
    FieldNameList,
    QuestionRequest,
    QuestionResult,
    StructuredDocument,
    StructuredStatus,
)
from app.structured.decisions import OpenAIDecisions
from app.structured.general import OpenAIGeneral
from app.structured.qa import QuestionEngine
from app.structured.service import StructuredDocumentService
from app.structured.voice import QuestionVoice

# How often a running question checks whether the browser stopped it.
STOP_POLL_SECONDS = 0.25


def enabled(request: Request) -> None:
    if not get_app_settings(request).app.structured_data_enabled:
        raise reject("STRUCTURED_DATA_DISABLED", "構造化データAPIは無効です。", 503)


def runtime(request: Request) -> QuestionEngine:
    if not hasattr(request.app.state, "structured_data"):
        settings = get_app_settings(request)
        database = get_database(request)
        chooser = OpenAIDecisions(settings, ApiBudget(database))
        writer = OpenAIGeneral(settings, ApiBudget(database))
        request.app.state.structured_data = QuestionEngine(
            StructuredDocumentService(database),
            chooser if chooser.configured else None,
            writer=writer if writer.configured else None,
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
        {
            "questionId": body.reply_to.question_id,
            "value": body.reply_to.value,
            "text": body.reply_to.text,
        }
        if body.reply_to is not None
        else None
    )
    started = time.monotonic()
    try:
        context = (
            {
                "documentIds": body.context.document_ids,
                "previous": body.context.previous,
                "previousAnswer": body.context.previous_answer,
            }
            if body.context is not None
            else None
        )
        work = asyncio.ensure_future(
            runtime(request).ask(body.text, reply, body.document_ids, context)
        )
        # The chat's stop button drops the request: the work still running (an AI call, or the
        # next one) is cancelled instead of finishing for nobody.
        while not work.done():
            await asyncio.wait({work}, timeout=STOP_POLL_SECONDS)
            if not work.done() and await request.is_disconnected():
                work.cancel()
                raise reject("QA_STOPPED", "質問を止めました。", 409)
        result = work.result()
    except Exception as error:
        chat_log(request, question_line(body, started, error=error))
        raise
    chat_log(request, question_line(body, started, result=result))
    return result


@router.post("/events", dependencies=[Depends(enabled)], status_code=204)
async def chat_event(request: Request, body: ChatEvent) -> Response:
    """Chat actions the server does not otherwise see (removing an exchange), for the log only."""
    chat_log(
        request,
        {
            "type": body.type,
            "conversationId": body.conversation_id,
            "turns": [turn.model_dump(by_alias=True) for turn in body.turns],
        },
    )
    return Response(status_code=204)


def question_line(
    body: QuestionRequest,
    started: float,
    result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "type": "reply" if body.reply_to is not None else "question",
        **(
            {
                "conversationId": body.client.conversation_id,
                "turnId": body.client.turn_id,
                "recovered": body.client.recovered,
            }
            if body.client is not None
            else {}
        ),
        "text": body.text,
        "replyTo": body.reply_to.model_dump(by_alias=True, exclude_none=True)
        if body.reply_to
        else None,
        "documentIds": body.document_ids,
        "context": body.context.model_dump(by_alias=True) if body.context else None,
        "ms": round((time.monotonic() - started) * 1000),
    }
    if result is not None:
        question = result.get("question")
        line["outcome"] = {
            "state": result.get("state"),
            "answer": result.get("answer"),
            "question": question
            and {
                "questionId": question.get("questionId"),
                "kind": question.get("kind"),
                "message": question.get("message"),
                "optional": question.get("optional"),
                "choices": [
                    {"value": choice.get("value"), "label": choice.get("label")}
                    for choice in question.get("choices") or []
                ],
            },
            "notes": result.get("notes"),
            "caveats": result.get("caveats"),
            "documents": result.get("documents"),
            "evidence": len(result.get("evidence") or []),
            "table": bool(result.get("table")),
            "modelCalls": result.get("modelCalls"),
            "general": result.get("general"),
            "open": result.get("open"),
        }
    elif isinstance(error, AppError):
        line["error"] = {"status": error.status, "code": error.code, "message": error.detail}
    elif error is not None:
        line["error"] = {"status": 500, "code": type(error).__name__, "message": str(error)[:500]}
    return line


def chat_log(request: Request, entry: dict[str, Any]) -> None:
    """Opt-in local log (VOICE_TRANSCRIPT_LOG): chat questions, answers or errors, removals.

    The deployed beta keeps the same lines in S3 instead (BETA_CHAT_LOG, cloud/chat_feedback).
    """
    settings = get_app_settings(request)
    line = {"at": datetime.now(UTC).isoformat(), **entry}
    sink = getattr(request.app.state, "chat_log_sink", None)
    if sink is not None:
        sink(line)
    if not settings.voice.transcript_log:
        return
    path = settings.storage.data_dir / "logs" / "chat-questions.jsonl"
    with suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


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
