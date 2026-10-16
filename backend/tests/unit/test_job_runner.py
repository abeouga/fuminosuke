from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable

from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode
from app.jobs.runner import JobRunner
from app.repositories.job_repository import JobRepository
from app.storage.local_storage import LocalStorageService


def test_start_resumes_jobs_left_in_the_persistent_queue(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    Base.metadata.create_all(database.engine)
    processed: list[str] = []
    try:
        with database.session() as session:
            queued = JobRepository(session).create(
                job_type=JobType.EXTRACT_DOCUMENT,
                request_hash="a" * 64,
                config_snapshot={},
                status=JobStatus.QUEUED,
            )

        async def run() -> None:
            runner = JobRunner(
                database=database,
                settings=settings,
                storage=storage,
                handler=lambda job_id: processed.append(job_id),
                worker_count=1,
            )
            await runner.start()
            try:
                for _ in range(100):
                    with database.session() as session:
                        current = JobRepository(session).get(queued.id)
                        if current and current.status == JobStatus.REVIEW_REQUIRED.value:
                            break
                    await asyncio.sleep(0.01)
            finally:
                await runner.stop()

        asyncio.run(run())
        assert processed == [queued.id]
        with database.session() as session:
            current = JobRepository(session).get(queued.id)
            assert current is not None
            assert current.status == JobStatus.REVIEW_REQUIRED.value
    finally:
        database.dispose()


def test_start_recovers_complete_created_job(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    Base.metadata.create_all(database.engine)
    processed: list[str] = []
    content = b"persisted-image-content"
    try:
        with database.session() as session:
            repository = JobRepository(session)
            created = repository.create(
                job_type=JobType.PREPARE_TEMPLATE,
                request_hash="b" * 64,
                config_snapshot={"name": "復旧対象"},
                mode=TemplateMode.REFERENCE_TEMPLATE,
            )
            relative_path = storage.write_bytes(
                f"uploads/{created.id}/original.png",
                content,
            )
            repository.add_input(
                job_id=created.id,
                input_index=0,
                media_type="image/png",
                format="png",
                width=10,
                height=10,
                file_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                relative_path=relative_path,
            )

        async def run() -> int:
            runner = JobRunner(
                database=database,
                settings=settings,
                storage=storage,
                handler=lambda job_id: processed.append(job_id),
                worker_count=1,
            )
            recovered = await runner.start()
            try:
                for _ in range(100):
                    with database.session() as session:
                        current = JobRepository(session).get(created.id)
                        if current and current.status == JobStatus.REVIEW_REQUIRED.value:
                            break
                    await asyncio.sleep(0.01)
            finally:
                await runner.stop()
            return recovered

        assert asyncio.run(run()) == 1
        assert processed == [created.id]
        with database.session() as session:
            current = JobRepository(session).get(created.id)
            assert current is not None
            assert current.status == JobStatus.REVIEW_REQUIRED.value
            assert current.error_code is None
    finally:
        database.dispose()


def test_start_marks_incomplete_created_job_failed(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    Base.metadata.create_all(database.engine)
    processed: list[str] = []
    original = b"expected-image-content"
    tampered = b"tampered-image-content"
    try:
        with database.session() as session:
            repository = JobRepository(session)
            created = repository.create(
                job_type=JobType.PREPARE_TEMPLATE,
                request_hash="c" * 64,
                config_snapshot={"name": "再登録可能"},
                mode=TemplateMode.REFERENCE_TEMPLATE,
            )
            relative_path = storage.write_bytes(
                f"uploads/{created.id}/original.png",
                tampered,
            )
            repository.add_input(
                job_id=created.id,
                input_index=0,
                media_type="image/png",
                format="png",
                width=10,
                height=10,
                file_bytes=len(tampered),
                sha256=hashlib.sha256(original).hexdigest(),
                relative_path=relative_path,
            )

        async def run() -> int:
            runner = JobRunner(
                database=database,
                settings=settings,
                storage=storage,
                handler=lambda job_id: processed.append(job_id),
                worker_count=1,
            )
            recovered = await runner.start()
            await runner.stop()
            return recovered

        assert asyncio.run(run()) == 1
        assert processed == []
        with database.session() as session:
            current = JobRepository(session).get(created.id)
            assert current is not None
            assert current.status == JobStatus.FAILED.value
            assert current.error_code == "INCOMPLETE_JOB_INPUTS"
            assert current.completed_at is not None
    finally:
        database.dispose()


def test_provider_error_after_cancel_request_finishes_as_cancelled(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            job = JobRepository(session).create(
                job_type=JobType.EXTRACT_DOCUMENT,
                request_hash="d" * 64,
                config_snapshot={},
                status=JobStatus.PROCESSING,
            )
            JobRepository(session).update(job.id, cancel_requested=True)
            job_id = job.id

        runner = JobRunner(
            database=database,
            settings=settings,
            storage=storage,
            handler=lambda _job_id: None,
            worker_count=1,
        )
        runner._fail(
            job_id,
            "JOB_CANCELLED",
            "読み取りは取り消されました。",
            stack_trace="synthetic cancellation trace",
        )

        with database.session() as session:
            cancelled = JobRepository(session).get(job_id)
            assert cancelled is not None
            assert cancelled.status == JobStatus.CANCELLED.value
            assert cancelled.error_code is None
            assert cancelled.error_message_safe is None
    finally:
        database.dispose()
