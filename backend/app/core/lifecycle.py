from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.logging import configure_logging
from app.db.session import Database
from app.jobs.handlers import PipelineJobHandler
from app.jobs.runner import JobRunner
from app.services.google_oauth_service import GoogleOAuthService
from app.services.google_sheets_sync_service import GoogleSheetsSyncService
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
    google_oauth = GoogleOAuthService(settings.google_oauth, database)
    google_sheets = GoogleSheetsSyncService(
        settings.google_sheets,
        google_oauth.credentials,
        google_oauth.spreadsheet_target if google_oauth.enabled else None,
        google_oauth.save_spreadsheet_target if google_oauth.enabled else None,
    )
    runner = JobRunner(
        database=database,
        settings=settings,
        storage=storage,
        handler=PipelineJobHandler(
            settings=settings,
            database=database,
            storage=storage,
            google_sheets=google_sheets,
        ),
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
    app.state.google_sheets = google_sheets
    app.state.google_oauth = google_oauth
    app.state.ocr_result_service = OcrResultService(database, storage, google_sheets)
    app.state.individual_record_service = IndividualRecordService(database)
    app.state.job_worker_status = "running"
    try:
        yield
    finally:
        await runner.stop()
        database.dispose()
