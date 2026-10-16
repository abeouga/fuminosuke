from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.core.errors import ApiModel
from app.db.models import ProcessingJob


class JobAccepted(ApiModel):
    job_id: str
    job_type: str
    status: str
    created_at: datetime


class JobError(ApiModel):
    code: str
    message: str
    stack_trace: str | None = None


class JobDetail(ApiModel):
    resource_revision: int | None = None
    id: str
    job_type: str
    status: str
    progress_stage: str | None
    progress_percent: int = Field(ge=0, le=100)
    attempt_number: int
    error: JobError | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


def job_accepted(job: ProcessingJob) -> JobAccepted:
    return JobAccepted(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        created_at=job.created_at,
    )


def job_detail(job: ProcessingJob, *, stack_trace: str | None = None) -> JobDetail:
    error = None
    if job.error_code:
        error = JobError(
            code=job.error_code,
            message=job.error_message_safe or "処理に失敗しました。",
            stack_trace=stack_trace,
        )
    return JobDetail(
        resource_revision=getattr(job, "resource_revision", None),
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress_stage=job.progress_stage,
        progress_percent=job.progress_percent,
        attempt_number=job.attempt_number,
        error=error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
