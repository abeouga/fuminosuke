from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.core.errors import ApiModel
from app.db.models import JobEvent, OcrResult, ProcessingJob

router = APIRouter(prefix="/development", tags=["development"])


class OpenAIUsageRecord(ApiModel):
    job_id: str
    job_type: str
    job_status: str
    attempt_number: int
    recorded_at: datetime
    model_id: str | None
    api_call_count: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimated_cost: str | None
    duration_ms: float | None
    request_id: str | None
    source: Literal["audit", "historical"]


class OpenAIUsageSummary(ApiModel):
    job_count: int
    record_count: int
    api_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: str | None


class OpenAIUsageReport(ApiModel):
    summary: OpenAIUsageSummary
    records: list[OpenAIUsageRecord]


@router.get(
    "/openai-usage",
    response_model=OpenAIUsageReport,
    response_model_by_alias=True,
)
def get_openai_usage(
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> OpenAIUsageReport:
    audit_rows = session.execute(
        select(JobEvent, ProcessingJob)
        .join(ProcessingJob, ProcessingJob.id == JobEvent.job_id)
        .where(JobEvent.event_code == "OPENAI_API_USAGE")
        .order_by(JobEvent.created_at.desc(), JobEvent.id.desc())
    ).all()
    has_audit_event = exists(
        select(JobEvent.id).where(
            JobEvent.job_id == OcrResult.job_id,
            JobEvent.event_code == "OPENAI_API_USAGE",
        )
    )
    historical_rows = session.execute(
        select(OcrResult, ProcessingJob)
        .join(ProcessingJob, ProcessingJob.id == OcrResult.job_id)
        .where(OcrResult.provider == "openai", ~has_audit_event)
        .order_by(OcrResult.updated_at.desc(), OcrResult.id.desc())
    ).all()

    records = [
        _audit_record(event, job)
        for event, job in audit_rows
    ] + [
        _historical_record(result, job)
        for result, job in historical_rows
    ]
    records.sort(key=lambda item: item.recorded_at, reverse=True)
    return OpenAIUsageReport(
        summary=_summary(records),
        records=records[:limit],
    )


def _audit_record(event: JobEvent, job: ProcessingJob) -> OpenAIUsageRecord:
    metadata = event.metadata_json
    input_tokens = _optional_int(metadata.get("inputTokens"))
    output_tokens = _optional_int(metadata.get("outputTokens"))
    return OpenAIUsageRecord(
        job_id=job.id,
        job_type=job.job_type,
        job_status=job.status,
        attempt_number=_optional_int(metadata.get("attemptNumber")) or job.attempt_number,
        recorded_at=event.created_at,
        model_id=_optional_str(metadata.get("modelId")),
        api_call_count=_optional_int(metadata.get("apiCallCount")) or 0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=(
            _optional_int(metadata.get("totalTokens"))
            if metadata.get("totalTokens") is not None
            else _token_total(input_tokens, output_tokens)
        ),
        estimated_cost=_decimal_string(metadata.get("estimatedCost")),
        duration_ms=_optional_float(metadata.get("durationMs")),
        request_id=_optional_str(metadata.get("requestId")),
        source="audit",
    )


def _historical_record(result: OcrResult, job: ProcessingJob) -> OpenAIUsageRecord:
    return OpenAIUsageRecord(
        job_id=job.id,
        job_type=job.job_type,
        job_status=job.status,
        attempt_number=job.attempt_number,
        recorded_at=result.updated_at,
        model_id=result.model_id,
        api_call_count=result.api_call_count,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=_token_total(result.input_tokens, result.output_tokens),
        estimated_cost=(str(result.estimated_cost) if result.estimated_cost is not None else None),
        duration_ms=None,
        request_id=None,
        source="historical",
    )


def _summary(records: list[OpenAIUsageRecord]) -> OpenAIUsageSummary:
    costs = [Decimal(item.estimated_cost) for item in records if item.estimated_cost is not None]
    return OpenAIUsageSummary(
        job_count=len({item.job_id for item in records}),
        record_count=len(records),
        api_call_count=sum(item.api_call_count for item in records),
        input_tokens=sum(item.input_tokens or 0 for item in records),
        output_tokens=sum(item.output_tokens or 0 for item in records),
        total_tokens=sum(item.total_tokens or 0 for item in records),
        estimated_cost=(str(sum(costs, Decimal("0"))) if costs else None),
    )


def _token_total(input_tokens: int | None, output_tokens: int | None) -> int | None:
    if input_tokens is None or output_tokens is None:
        return None
    return input_tokens + output_tokens


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _decimal_string(value: object) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)))
    except InvalidOperation:
        return None
