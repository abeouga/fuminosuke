from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import inspect

from app.ai.api_budget import ApiBudget
from app.api.dependencies import get_app_settings, get_database, get_storage
from app.api.editing_transport import EditingRoute, Token, local_request
from app.assistant.conversation import ConversationController
from app.assistant.conversation_contracts import (
    ConversationStart,
    ResolveTurn,
    StartedTurn,
    TurnResolution,
    TurnStatus,
)
from app.assistant.conversation_provider import ConversationProvider
from app.editing.contracts import (
    Batch,
    BatchResult,
    CatalogResult,
    CloseResult,
    ContextResult,
    ContextUpdate,
    EditingStatus,
    HistoryResult,
    ReceiptResult,
    SessionCreate,
    SessionResult,
    TargetSearch,
    Undo,
)
from app.editing.errors import reject
from app.editing.manual_contracts import (
    ManualEdit,
    ManualOcrEdit,
    ManualReceipt,
    ManualResult,
    ManualView,
)
from app.editing.service import EditingService


def enabled(request: Request) -> None:
    if not get_app_settings(request).app.editing_api_enabled:
        raise reject(
            "EDITING_API_DISABLED", "新しい編集APIはまだ通常利用へ切り替えていません。", 503
        )
    tables = inspect(get_database(request).engine).get_table_names()
    if not {"editing_sessions", "editing_turns", "editing_operations"} <= set(tables):
        raise reject("EDITING_SCHEMA_REQUIRED", "新しい編集契約のDB移行が必要です。", 503)


router = APIRouter(
    prefix="/editing",
    tags=["editing"],
    route_class=EditingRoute,
    dependencies=[Depends(local_request)],
)


def service(request: Request) -> EditingService:
    return EditingService(get_database(request), get_storage(request))


def controller(request: Request) -> ConversationController:
    # All conversation routes run on the app event loop. Keep only bounded,
    # expiring interpretation state; business state remains in EditingService.
    if not hasattr(request.app.state, "editing_conversation"):
        editing = service(request)
        request.app.state.editing_conversation = ConversationController(
            editing, ConversationProvider(get_app_settings(request), ApiBudget(editing.database))
        )
    return request.app.state.editing_conversation  # type: ignore[no-any-return]


@router.get("/status", response_model=EditingStatus)
def status(request: Request) -> dict[str, Any]:
    return {
        "contractVersion": "editing.v1",
        "enabled": get_app_settings(request).app.editing_api_enabled,
    }


@router.post(
    "/sessions",
    dependencies=[Depends(enabled)],
    response_model=SessionResult,
    response_model_exclude_unset=True,
)
def create_session(request: Request, body: SessionCreate) -> dict[str, Any]:
    return service(request).create_session(body)


@router.get(
    "/sessions/{session_id}/context",
    dependencies=[Depends(enabled)],
    response_model=ContextResult,
    response_model_exclude_unset=True,
)
def context(request: Request, session_id: str, token: Token) -> dict[str, Any]:
    return service(request).context(session_id, token)


@router.put(
    "/sessions/{session_id}/context",
    dependencies=[Depends(enabled)],
    response_model=ContextResult,
    response_model_exclude_unset=True,
)
def update_context(
    request: Request, session_id: str, token: Token, body: ContextUpdate
) -> dict[str, Any]:
    return service(request).update_context(session_id, token, body)


@router.get(
    "/sessions/{session_id}/manual-view",
    dependencies=[Depends(enabled)],
    response_model=ManualView,
    response_model_exclude_unset=True,
)
def manual_view(request: Request, session_id: str, token: Token) -> dict[str, Any]:
    return service(request).manual_view(session_id, token)


@router.post(
    "/sessions/{session_id}/manual-edits",
    dependencies=[Depends(enabled)],
    response_model=ManualResult,
)
def manual_edit(
    request: Request, session_id: str, token: Token, body: ManualEdit
) -> dict[str, Any]:
    return service(request).manual_apply(session_id, token, body)


@router.post(
    "/sessions/{session_id}/manual-ocr-edits",
    dependencies=[Depends(enabled)],
    response_model=BatchResult,
)
def manual_ocr_edit(
    request: Request, session_id: str, token: Token, body: ManualOcrEdit
) -> dict[str, Any]:
    return service(request).manual_ocr_apply(session_id, token, body)


@router.get(
    "/sessions/{session_id}/manual-receipts/{request_id}",
    dependencies=[Depends(enabled)],
    response_model=ManualReceipt,
)
def manual_receipt(
    request: Request, session_id: str, request_id: str, token: Token
) -> dict[str, Any]:
    return service(request).manual_receipt(session_id, token, request_id)


@router.post(
    "/sessions/{session_id}/targets:find",
    dependencies=[Depends(enabled)],
    response_model=CatalogResult,
    response_model_exclude_unset=True,
)
def find(request: Request, session_id: str, token: Token, body: TargetSearch) -> dict[str, Any]:
    return service(request).find(session_id, token, body)


@router.post(
    "/sessions/{session_id}/batches", dependencies=[Depends(enabled)], response_model=BatchResult
)
def apply(request: Request, session_id: str, token: Token, body: Batch) -> dict[str, Any]:
    return service(request).apply(session_id, token, body)


@router.post(
    "/sessions/{session_id}/undo", dependencies=[Depends(enabled)], response_model=BatchResult
)
def undo(request: Request, session_id: str, token: Token, body: Undo) -> dict[str, Any]:
    return service(request).undo(session_id, token, body)


@router.get(
    "/sessions/{session_id}/receipts/{request_id}",
    dependencies=[Depends(enabled)],
    response_model=ReceiptResult,
)
def receipt(request: Request, session_id: str, request_id: str, token: Token) -> dict[str, Any]:
    return service(request).receipt(session_id, token, request_id)


@router.get(
    "/sessions/{session_id}/history", dependencies=[Depends(enabled)], response_model=HistoryResult
)
def history(
    request: Request, session_id: str, token: Token, limit: Annotated[int, Query(ge=1, le=20)] = 20
) -> dict[str, Any]:
    return service(request).history(session_id, token, limit)


@router.delete(
    "/sessions/{session_id}", dependencies=[Depends(enabled)], response_model=CloseResult
)
async def close(request: Request, session_id: str, token: Token) -> dict[str, str]:
    result = service(request).close(session_id, token)
    streams = getattr(request.app.state, "editing_streams", None)
    if streams is not None:
        await streams.close(session_id)
    if hasattr(request.app.state, "editing_conversation"):
        controller(request).clear(session_id)
    return result


@router.post(
    "/sessions/{session_id}/conversation/turns",
    dependencies=[Depends(enabled)],
    response_model=StartedTurn,
    response_model_exclude_unset=True,
)
async def start_conversation_turn(
    request: Request, session_id: str, token: Token, body: ConversationStart
) -> StartedTurn:
    return controller(request).start(session_id, token, body)


@router.post(
    "/sessions/{session_id}/conversation/turns/{turn_id}/resolve",
    dependencies=[Depends(enabled)],
    response_model=TurnResolution,
    response_model_exclude_unset=True,
)
async def resolve_conversation_turn(
    request: Request, session_id: str, turn_id: str, token: Token, body: ResolveTurn
) -> TurnResolution:
    return await controller(request).resolve(session_id, token, turn_id, body)


@router.get(
    "/sessions/{session_id}/conversation/turns/{turn_id}",
    dependencies=[Depends(enabled)],
    response_model=TurnStatus,
)
async def conversation_turn_status(
    request: Request, session_id: str, turn_id: str, token: Token
) -> dict[str, Any]:
    return service(request).turn_status(session_id, token, turn_id)


@router.delete(
    "/sessions/{session_id}/conversation/turns/{turn_id}",
    dependencies=[Depends(enabled)],
    response_model=TurnStatus,
)
async def cancel_conversation_turn(
    request: Request, session_id: str, turn_id: str, token: Token
) -> dict[str, Any]:
    return controller(request).cancel(session_id, token, turn_id)
