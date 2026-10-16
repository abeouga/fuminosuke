from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.logging import configure_logging
from app.db.session import Database
from app.jobs.handlers import PipelineJobHandler
from app.jobs.runner import JobRunner
from app.services.artifact_recovery_service import ArtifactRecoveryService
from app.services.document_ocr_execution_service import DocumentOcrExecutionService
from app.services.extraction_service import ImageExtractionService
from app.services.job_service import JobService
from app.services.ocr_result_service import OcrResultService
from app.services.ocr_workflow_service import OcrWorkflowService
from app.services.template_service import TemplatePreparationService
from app.storage.local_storage import LocalStorageService
from app.template_packages.operation_service import TemplatePackageOperationService

if TYPE_CHECKING:
    from fastapi import FastAPI


async def start_runtime_services(app: FastAPI) -> None:
    settings = app.state.settings
    storage: LocalStorageService = app.state.storage
    database: Database = app.state.database
    artifact_recovery = ArtifactRecoveryService(
        settings=settings,
        database=database,
        storage=storage,
    )
    ocr_result_service = OcrResultService(database, storage)
    template_service = TemplatePreparationService(
        settings=settings,
        database=database,
        storage=storage,
    )
    extraction_service = ImageExtractionService(
        settings=settings,
        database=database,
        storage=storage,
    )
    ocr_workflow_service = OcrWorkflowService(
        settings=settings,
        database=database,
        storage=storage,
    )
    from app.services.job_page_service import JobPageService
    from app.services.paged_ocr_execution import PagedOcrExecution

    document_ocr_execution = DocumentOcrExecutionService(
        database=database,
        extraction_service=extraction_service,
        ocr_workflow_service=ocr_workflow_service,
        paged_execution=PagedOcrExecution(
            JobPageService(database, storage, settings),
            extraction_service,
            ocr_workflow_service,
        ),
    )
    pipeline_handler = PipelineJobHandler(
        database=database,
        template_service=template_service,
        document_ocr_execution=document_ocr_execution,
    )
    runner = JobRunner(
        database=database,
        settings=settings,
        storage=storage,
        handler=pipeline_handler,
        worker_count=settings.jobs.max_concurrency,
        execution_gate=getattr(app.state, "job_gate", None),
    )
    await runner.start()
    app.state.job_runner = runner
    app.state.job_service = JobService(
        settings=settings,
        database=database,
        storage=storage,
        runner=runner,
    )
    app.state.artifact_recovery = artifact_recovery
    app.state.ocr_result_service = ocr_result_service
    app.state.job_worker_status = "running"


async def stop_runtime_services(app: FastAPI) -> None:
    app.state.job_worker_status = "not_started"
    runner = getattr(app.state, "job_runner", None)
    if runner is not None:
        await runner.stop()


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    access_check = getattr(app.state, "access_check", None)
    storage = LocalStorageService(settings.storage.data_dir, access_check=access_check)
    storage.initialize()
    if not getattr(app.state, "shared_logging", False):
        configure_logging(settings.logging)
    database = Database(
        settings.database, access_check=access_check,
        api_budget=getattr(app.state, "api_budget", None),
        before_delete=getattr(app.state, "before_delete", None),
    )
    database.check_connection()
    app.state.storage = storage
    app.state.database = database
    app.state.template_package_operations = TemplatePackageOperationService(
        database,
        storage,
    )
    await start_runtime_services(app)
    try:
        yield
    finally:
        template_analysis = getattr(app.state, "template_analysis", None)
        if template_analysis is not None:
            await template_analysis.close()
        editing_v2 = getattr(app.state, "editing_v2", None)
        if editing_v2 is not None:
            await editing_v2.close()
        streams = getattr(app.state, "editing_streams", None)
        if streams is not None:
            await streams.close()
        await stop_runtime_services(app)
        app.state.template_package_operations.close()
        database.dispose()
