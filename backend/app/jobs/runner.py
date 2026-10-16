from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import traceback
from datetime import UTC, datetime

import anyio

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import JobInput, ProcessingJob
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode
from app.domain.job_inputs import job_input_bounds
from app.domain.template_config import TemplateConfig
from app.jobs.handlers import JobHandler
from app.jobs.queue import JobQueue
from app.repositories.job_repository import JobRepository
from app.storage.local_storage import LocalStorageService, StoragePathError

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        storage: LocalStorageService,
        handler: JobHandler,
        worker_count: int,
        queue_size: int = 100,
        execution_gate: asyncio.Semaphore | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._storage = storage
        self._handler = handler
        self._worker_count = worker_count
        self._queue = JobQueue(max_size=queue_size)
        self._tasks: list[asyncio.Task[None]] = []
        self._execution_gate = execution_gate

    async def start(self) -> int:
        with self._database.session() as session:
            repository = JobRepository(session)
            recovered_created, failed_created = repository.recover_created(
                self._created_job_is_complete
            )
            recovered_interrupted = repository.recover_interrupted()
            queued_job_ids = [
                job.id for job in repository.list_by_status(JobStatus.QUEUED)
            ]
        self._tasks = [
            asyncio.create_task(self._worker(index), name=f"ocr-job-worker-{index}")
            for index in range(self._worker_count)
        ]
        for job_id in queued_job_ids:
            await self._queue.put(job_id)
        return recovered_created + failed_created + recovered_interrupted

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    @property
    def is_running(self) -> bool:
        return bool(self._tasks) and all(not task.done() for task in self._tasks)

    async def _worker(self, worker_index: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                if self._execution_gate is None:
                    await self._execute(job_id)
                else:
                    async with self._execution_gate:
                        await self._execute(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Unexpected worker loop failure",
                    extra={"jobId": job_id, "workerIndex": worker_index},
                )
            finally:
                self._queue.task_done()

    async def _execute(self, job_id: str) -> None:
        if not self._begin(job_id):
            return
        try:
            await anyio.to_thread.run_sync(self._handler, job_id)
            self._finish(job_id)
        except AppError as exc:
            self._fail(job_id, exc.code, exc.detail, self._stack_trace(exc))
        except Exception as exc:
            logger.exception("Job processing failed", extra={"jobId": job_id})
            self._fail(
                job_id,
                "PROCESSING_FAILED",
                "処理に失敗しました。再試行できます。",
                self._stack_trace(exc),
            )

    @staticmethod
    def _stack_trace(exc: Exception) -> str:
        trace = exc.__traceback__
        frames = traceback.extract_tb(trace)[-40:] if trace is not None else []
        return "".join([
            "Traceback (most recent call last):\n",
            *traceback.format_list(frames),
            f"{type(exc).__name__}\n",
        ])

    def _begin(self, job_id: str) -> bool:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None or job.status != JobStatus.QUEUED.value:
                return False
            if job.cancel_requested:
                repository.update(
                    job_id,
                    status=JobStatus.CANCELLED,
                    completed_at=datetime.now(UTC),
                )
                return False
            repository.update(
                job_id,
                status=JobStatus.PROCESSING,
                progress_stage="VALIDATING_INPUT",
                progress_percent=1,
                started_at=datetime.now(UTC),
                completed_at=None,
            )
            repository.add_event(
                job_id=job_id,
                level="INFO",
                event_code="JOB_PROCESSING_STARTED",
                stage="VALIDATING_INPUT",
                metadata={"attemptNumber": job.attempt_number},
            )
            return True

    def _finish(self, job_id: str) -> None:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None or job.status != JobStatus.PROCESSING.value:
                return
            target = JobStatus.CANCELLED if job.cancel_requested else JobStatus.REVIEW_REQUIRED
            repository.update(
                job_id,
                status=target,
                progress_stage="READY_FOR_REVIEW" if target is JobStatus.REVIEW_REQUIRED else None,
                progress_percent=100,
                completed_at=datetime.now(UTC),
            )
            repository.add_event(
                job_id=job_id,
                level="INFO",
                event_code=(
                    "JOB_CANCELLED"
                    if target is JobStatus.CANCELLED
                    else "JOB_READY_FOR_REVIEW"
                ),
                stage="READY_FOR_REVIEW" if target is JobStatus.REVIEW_REQUIRED else None,
                metadata={"attemptNumber": job.attempt_number},
            )

    def _fail(self, job_id: str, code: str, safe_message: str, stack_trace: str) -> None:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None:
                return
            if job.cancel_requested or code == "JOB_CANCELLED":
                repository.update(
                    job_id,
                    status=JobStatus.CANCELLED,
                    error_code=None,
                    error_message_safe=None,
                    completed_at=datetime.now(UTC),
                )
                repository.add_event(
                    job_id=job_id,
                    level="INFO",
                    event_code="JOB_CANCELLED",
                    stage=job.progress_stage,
                    metadata={"attemptNumber": job.attempt_number},
                )
                return
            job.status = JobStatus.FAILED.value
            job.error_code = code
            job.error_message_safe = safe_message
            job.completed_at = datetime.now(UTC)
            repository.add_event(
                job_id=job_id,
                level="ERROR",
                event_code=code,
                stage=job.progress_stage,
                metadata={"attemptNumber": job.attempt_number, "stackTrace": stack_trace},
            )

    def _created_job_is_complete(
        self,
        job: ProcessingJob,
        inputs: list[JobInput],
    ) -> bool:
        try:
            job_type = JobType(job.job_type)
            mode = (
                TemplateMode(job.mode or "")
                if job_type is JobType.PREPARE_TEMPLATE
                else None
            )
            minimum, maximum = job_input_bounds(job_type, mode)
        except ValueError:
            return False

        if not minimum <= len(inputs) <= maximum:
            return False
        ordered = sorted(inputs, key=lambda item: item.input_index)
        if [item.input_index for item in ordered] != list(range(len(ordered))):
            return False

        upload_root = self._storage.resolve(f"uploads/{job.id}")
        for item in ordered:
            if (
                item.file_bytes <= 0
                or item.width <= 0
                or item.height <= 0
                or item.width > self._settings.image_processing.max_image_edge
                or item.height > self._settings.image_processing.max_image_edge
                or item.width * item.height
                > self._settings.image_processing.max_image_pixels
                or item.media_type not in {"image/jpeg", "image/png"}
                or item.format not in {"jpeg", "png"}
            ):
                return False
            try:
                path = self._storage.resolve(item.relative_path)
                path.relative_to(upload_root)
                if not path.is_file() or path.stat().st_size != item.file_bytes:
                    return False
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            except (OSError, StoragePathError, ValueError):
                return False
            if digest != item.sha256:
                return False
        return (
            job_type is not JobType.EXTRACT_DOCUMENT
            or self._ocr_template_snapshot_is_complete(job)
        )

    def _ocr_template_snapshot_is_complete(self, job: ProcessingJob) -> bool:
        raw_snapshot = job.config_snapshot_json.get("templateSnapshot")
        if not isinstance(raw_snapshot, dict):
            return False
        relative_path = raw_snapshot.get("cleanTemplatePath")
        expected_sha256 = raw_snapshot.get("cleanTemplateSha256")
        source_sha256 = raw_snapshot.get("sourceArtifactSha256")
        raw_config = raw_snapshot.get("config")
        expected_config_sha256 = raw_snapshot.get("configSha256")
        if (
            not isinstance(relative_path, str)
            or not isinstance(expected_sha256, str)
            or source_sha256 != expected_sha256
            or not isinstance(raw_config, dict)
            or not isinstance(expected_config_sha256, str)
        ):
            return False
        try:
            snapshot_root = self._storage.resolve(
                f"uploads/{job.id}/template_snapshot"
            )
            path = self._storage.resolve(relative_path)
            path.relative_to(snapshot_root)
            if not path.is_file():
                return False
            with path.open("rb") as stream:
                actual_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual_sha256 != expected_sha256:
                return False
            config = TemplateConfig.model_validate(raw_config)
            canonical = json.dumps(
                config.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (OSError, StoragePathError, ValueError):
            return False
        return hashlib.sha256(canonical).hexdigest() == expected_config_sha256
