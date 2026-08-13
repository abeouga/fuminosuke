from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.logging import configure_logging
from app.db.session import Database
from app.jobs.handlers import PipelineJobHandler
from app.jobs.runner import JobRunner
from app.services.individual_record_service import IndividualRecordService
from app.services.job_service import JobService
from app.services.ocr_result_service import OcrResultService
from app.storage.local_storage import LocalStorageService

if TYPE_CHECKING:
    from fastapi import FastAPI


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    configure_logging(settings.logging)
    database = Database(settings.database)
    database.check_connection()
    runner = JobRunner(
        database=database,
        settings=settings,
        storage=storage,
        handler=PipelineJobHandler(settings=settings, database=database, storage=storage),
        worker_count=settings.jobs.max_concurrency,
    )
    await runner.start()

    app.state.storage = storage
    app.state.database = database
    app.state.job_runner = runner
    app.state.job_service = JobService(
        settings=settings,
        database=database,
        storage=storage,
        runner=runner,
    )
    app.state.ocr_result_service = OcrResultService(database, storage)
    app.state.individual_record_service = IndividualRecordService(database)
    app.state.job_worker_status = "running"
    try:
        yield
    finally:
        await runner.stop()
        database.dispose()
