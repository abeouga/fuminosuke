from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import anyio

from app.core.errors import AppError
from app.db.session import Database
from app.domain.enums import JobStatus
from app.jobs.handlers import JobHandler
from app.jobs.queue import JobQueue
from app.repositories.job_repository import JobRepository

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        *,
        database: Database,
        handler: JobHandler,
        worker_count: int,
        queue_size: int = 100,
    ) -> None:
        self._database = database
        self._handler = handler
        self._worker_count = worker_count
        self._queue = JobQueue(max_size=queue_size)
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> int:
        with self._database.session() as session:
            recovered = JobRepository(session).recover_interrupted()
        self._tasks = [
            asyncio.create_task(self._worker(index), name=f"ocr-job-worker-{index}")
            for index in range(self._worker_count)
        ]
        return recovered

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
            self._fail(job_id, exc.code, exc.detail)
        except Exception:
            logger.exception("Job processing failed", extra={"jobId": job_id})
            self._fail(job_id, "PROCESSING_FAILED", "処理に失敗しました。再試行できます。")

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

    def _fail(self, job_id: str, code: str, safe_message: str) -> None:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None:
                return
            repository.update(
                job_id,
                status=JobStatus.FAILED,
                error_code=code,
                error_message_safe=safe_message,
                completed_at=datetime.now(UTC),
            )
            repository.add_event(
                job_id=job_id,
                level="ERROR",
                event_code=code,
                stage=job.progress_stage,
                metadata={"attemptNumber": job.attempt_number},
            )
