from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models import JobEvent, JobInput, ProcessingJob
from app.domain.enums import JobStatus, JobType, TemplateMode


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        job_type: JobType,
        request_hash: str,
        config_snapshot: Mapping[str, Any],
        status: JobStatus = JobStatus.CREATED,
        template_version_id: str | None = None,
        mode: TemplateMode | None = None,
        idempotency_key: str | None = None,
        image_sha256: str | None = None,
    ) -> ProcessingJob:
        job = ProcessingJob(
            job_type=job_type.value,
            status=status.value,
            template_version_id=template_version_id,
            mode=mode.value if mode else None,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            image_sha256=image_sha256,
            attempt_number=1,
            progress_percent=0,
            cancel_requested=False,
            config_snapshot_json=dict(config_snapshot),
        )
        self._session.add(job)
        self._session.commit()
        self._session.refresh(job)
        return job

    def get(self, job_id: str) -> ProcessingJob | None:
        return self._session.get(ProcessingJob, job_id)

    def find_idempotent(self, idempotency_key: str, request_hash: str) -> ProcessingJob | None:
        statement = select(ProcessingJob).where(
            ProcessingJob.idempotency_key == idempotency_key,
            ProcessingJob.request_hash == request_hash,
        )
        return self._session.scalar(statement)

    def find_by_idempotency_key(self, idempotency_key: str) -> ProcessingJob | None:
        statement = select(ProcessingJob).where(
            ProcessingJob.idempotency_key == idempotency_key
        )
        return self._session.scalar(statement)

    def list_by_status(self, status: JobStatus) -> list[ProcessingJob]:
        statement = select(ProcessingJob).where(ProcessingJob.status == status.value)
        return list(self._session.scalars(statement))

    def list_by_statuses(
        self,
        statuses: Collection[JobStatus],
        *,
        limit: int = 1000,
    ) -> list[ProcessingJob]:
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid pagination")
        values = [status.value for status in statuses]
        if not values:
            return []
        statement: Select[tuple[ProcessingJob]] = (
            select(ProcessingJob)
            .where(ProcessingJob.status.in_(values))
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc())
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def add_input(
        self,
        *,
        job_id: str,
        input_index: int,
        media_type: str,
        format: str,
        width: int,
        height: int,
        file_bytes: int,
        sha256: str,
        relative_path: str,
    ) -> JobInput:
        item = JobInput(
            job_id=job_id,
            input_index=input_index,
            media_type=media_type,
            format=format,
            width=width,
            height=height,
            file_bytes=file_bytes,
            sha256=sha256,
            relative_path=relative_path,
        )
        self._session.add(item)
        self._session.commit()
        self._session.refresh(item)
        return item

    def list_inputs(self, job_id: str) -> list[JobInput]:
        statement = (
            select(JobInput).where(JobInput.job_id == job_id).order_by(JobInput.input_index)
        )
        return list(self._session.scalars(statement))

    def add_event(
        self,
        *,
        job_id: str,
        level: str,
        event_code: str,
        stage: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> JobEvent:
        event = JobEvent(
            job_id=job_id,
            level=level,
            event_code=event_code,
            stage=stage,
            metadata_json=dict(metadata or {}),
        )
        self._session.add(event)
        self._session.commit()
        self._session.refresh(event)
        return event

    def recover_interrupted(self) -> int:
        jobs = self.list_by_status(JobStatus.PROCESSING)
        now = datetime.now(UTC)
        for job in jobs:
            job.status = JobStatus.INTERRUPTED.value
            job.error_code = "BACKEND_RESTARTED"
            job.error_message_safe = (
                "バックエンド再起動により処理が中断されました。再試行できます。"
            )
            job.completed_at = now
            self._session.add(
                JobEvent(
                    job_id=job.id,
                    level="WARNING",
                    event_code="BACKEND_RESTARTED",
                    stage=job.progress_stage,
                    metadata_json={"attemptNumber": job.attempt_number},
                )
            )
        self._session.commit()
        return len(jobs)

    def recover_created(
        self,
        is_complete: Callable[[ProcessingJob, list[JobInput]], bool],
    ) -> tuple[int, int]:
        jobs = self.list_by_status(JobStatus.CREATED)
        now = datetime.now(UTC)
        queued = 0
        failed = 0
        for job in jobs:
            inputs = self.list_inputs(job.id)
            if is_complete(job, inputs):
                job.status = JobStatus.QUEUED.value
                job.progress_stage = None
                job.progress_percent = 0
                job.error_code = None
                job.error_message_safe = None
                job.completed_at = None
                event_code = "CREATED_JOB_RECOVERED"
                level = "INFO"
                queued += 1
            else:
                job.status = JobStatus.FAILED.value
                job.error_code = "INCOMPLETE_JOB_INPUTS"
                job.error_message_safe = (
                    "受付中にバックエンドが停止し、元画像の保存を完了できませんでした。"
                    "同じ内容を新しいジョブとして登録してください。"
                )
                job.completed_at = now
                event_code = "INCOMPLETE_JOB_INPUTS"
                level = "ERROR"
                failed += 1
            self._session.add(
                JobEvent(
                    job_id=job.id,
                    level=level,
                    event_code=event_code,
                    stage=job.progress_stage,
                    metadata_json={
                        "attemptNumber": job.attempt_number,
                        "inputCount": len(inputs),
                    },
                )
            )
        self._session.commit()
        return queued, failed

    def list(self, *, offset: int = 0, limit: int = 100) -> list[ProcessingJob]:
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid pagination")
        statement: Select[tuple[ProcessingJob]] = (
            select(ProcessingJob)
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def update(self, job_id: str, **changes: object) -> ProcessingJob | None:
        allowed = {
            "status",
            "progress_stage",
            "progress_percent",
            "cancel_requested",
            "attempt_number",
            "error_code",
            "error_message_safe",
            "started_at",
            "completed_at",
            "confirmed_at",
            "config_snapshot_json",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported job fields: {', '.join(sorted(unknown))}")
        job = self.get(job_id)
        if job is None:
            return None
        for field, value in changes.items():
            if field == "status" and isinstance(value, JobStatus):
                value = value.value
            setattr(job, field, value)
        self._session.commit()
        self._session.refresh(job)
        return job

    def delete(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        self._session.delete(job)
        self._session.commit()
        return True
