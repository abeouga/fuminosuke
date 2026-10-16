from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import inspect

from app.ai.api_budget import ApiBudget
from app.api.dependencies import get_app_settings, get_database, get_storage
from app.api.editing_transport import EditingRoute, Token, local_request
from app.api.v2.editing import enabled as editing_enabled
from app.api.v2.editing import runtime as editing_runtime
from app.editing.errors import reject
from app.template_analysis.contracts import (
    AnalysisCreate,
    AnalysisEventResult,
    AnalysisMergeRequest,
    AnalysisPlanResult,
    AnalysisResult,
    AnalysisRevertRequest,
    MergeResult,
)
from app.template_analysis.models import TABLES
from app.template_analysis.repository import ACTIVE
from app.template_analysis.service import AnalysisService


class AnalysisRoute(EditingRoute):
    def body_limit(self, request: Request) -> int:
        return 2_097_152


def enabled(request: Request) -> None:
    if not get_app_settings(request).app.template_analysis_enabled:
        raise reject("TEMPLATE_ANALYSIS_DISABLED", "AI意味付けは試験用環境だけで有効です。", 503)
    editing_enabled(request)
    names = set(inspect(get_database(request).engine).get_table_names())
    if any(str(t) not in names for t in TABLES):
        raise reject("TEMPLATE_ANALYSIS_SCHEMA_REQUIRED", "AI意味付けのDB移行が必要です。", 503)


def runtime(request: Request) -> AnalysisService:
    if not hasattr(request.app.state, "template_analysis"):
        database = get_database(request)
        request.app.state.template_analysis = AnalysisService(
            editing_runtime(request),
            get_storage(request),
            get_app_settings(request),
            ApiBudget(database),
        )
    return cast(AnalysisService, request.app.state.template_analysis)


router = APIRouter(
    prefix="/api/v2/editing/sessions/{session_id}/template-analyses",
    tags=["template-analysis.v3"],
    route_class=AnalysisRoute,
    dependencies=[Depends(local_request), Depends(enabled)],
)


@router.post("", status_code=202, response_model=AnalysisResult)
async def create(
    request: Request, session_id: str, token: Token, body: AnalysisCreate
) -> dict[str, Any]:
    return await runtime(request).create(session_id, token, body)


@router.post("/plan", response_model=AnalysisPlanResult)
async def plan(
    request: Request, session_id: str, token: Token, body: AnalysisCreate
) -> dict[str, Any]:
    return await asyncio.to_thread(runtime(request).plan, session_id, token, body)


@router.get("/{analysis_id}", response_model=AnalysisResult)
async def get(request: Request, session_id: str, analysis_id: str, token: Token) -> dict[str, Any]:
    return await asyncio.to_thread(runtime(request).repository.get, session_id, token, analysis_id)


@router.post("/{analysis_id}/cancel", response_model=AnalysisResult)
async def cancel(
    request: Request, session_id: str, analysis_id: str, token: Token
) -> dict[str, Any]:
    return await asyncio.to_thread(
        runtime(request).repository.cancel, session_id, token, analysis_id
    )


@router.post("/{analysis_id}/merge", response_model=MergeResult)
async def merge(
    request: Request, session_id: str, analysis_id: str, token: Token, body: AnalysisMergeRequest
) -> dict[str, Any]:
    return await asyncio.to_thread(runtime(request).merge, session_id, token, analysis_id, body)


@router.post("/{analysis_id}/merges/{merge_id}/revert", response_model=AnalysisResult)
async def revert_merge(
    request: Request,
    session_id: str,
    analysis_id: str,
    merge_id: str,
    token: Token,
    body: AnalysisRevertRequest,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        runtime(request).revert_merge, session_id, token, analysis_id, merge_id, body
    )


@router.get("/{analysis_id}/events")
async def events(
    request: Request,
    session_id: str,
    analysis_id: str,
    token: Token,
    last_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=180)] = None,
) -> StreamingResponse:
    service = runtime(request)
    after = 0
    if last_id:
        try:
            owner, sequence = last_id.rsplit(":", 1)
            if owner != analysis_id or int(sequence) < 0:
                raise ValueError()
            after = int(sequence)
        except ValueError as error:
            raise reject("INVALID_ARGUMENT", "イベント再開位置が不正です。", 422) from error
    initial = await asyncio.to_thread(service.repository.get, session_id, token, analysis_id)
    if after > initial["lastSequence"]:
        raise reject("INVALID_ARGUMENT", "イベント再開位置が現在の記録を超えています。", 422)

    async def stream() -> AsyncIterator[str]:
        nonlocal after
        while not await request.is_disconnected():
            items, state = await asyncio.to_thread(
                service.repository.events, session_id, token, analysis_id, after
            )
            for item in items:
                after = item["sequence"]
                item = AnalysisEventResult.model_validate(item).model_dump(
                    mode="json", by_alias=True, exclude_unset=True
                )
                data = json.dumps(item, ensure_ascii=False)
                yield f"id: {analysis_id}:{after}\nevent: analysis\ndata: {data}\n\n"
            if state not in ACTIVE and not items:
                return
            if not items:
                yield ": keepalive\n\n"
                await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )
