from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.logging import configure_logging
from app.db.session import Database
from app.jobs.handlers import PipelineJobHandler
from app.jobs.runner import JobRunner
from app.services.artifact_recovery_service import ArtifactRecoveryService
from app.services.data_search_service import DataSearchService
from app.services.document_library_service import DocumentLibraryService
from app.services.document_ocr_execution_service import DocumentOcrExecutionService
from app.services.extraction_service import ImageExtractionService
from app.services.google_drive_service import GoogleDriveService
from app.services.google_oauth_service import GoogleOAuthService
from app.services.google_sheets_edit_worker import GoogleSheetsEditWorker
from app.services.google_sheets_sync_service import GoogleSheetsSyncService
from app.services.individual_record_service import IndividualRecordService
from app.services.job_service import JobService
from app.services.ocr_result_service import OcrResultService
from app.services.ocr_workflow_service import OcrWorkflowService
from app.services.output_layout_template_service import OutputLayoutTemplateService
from app.services.portable_backup_service import PortableBackupService
from app.services.portable_restore_service import PortableRestoreService
from app.services.template_service import TemplatePreparationService
from app.services.workspace_cloud_sync_service import WorkspaceCloudSyncService
from app.storage.local_storage import LocalStorageService
from app.template_packages.drive_sync_service import TemplatePackageDriveSyncService
from app.template_packages.operation_service import TemplatePackageOperationService

if TYPE_CHECKING:
    from fastapi import FastAPI


async def start_runtime_services(app: FastAPI) -> None:
    settings = app.state.settings
    storage: LocalStorageService = app.state.storage
    database: Database = app.state.database
    google_oauth = GoogleOAuthService(settings.google_oauth, database)
    google_drive = GoogleDriveService(
        database,
        google_oauth.credentials,
        google_oauth.active_subject,
        settings.google_oauth.picker_api_key,
        settings.google_oauth.picker_app_id,
        settings.google_oauth.picker_project_matches,
        settings.google_oauth.picker_configuration_error,
    )
    output_layout_templates = OutputLayoutTemplateService(
        database,
        google_oauth.active_subject if google_oauth.enabled else None,
    )
    google_sheets = GoogleSheetsSyncService(
        settings.google_sheets,
        google_oauth.credentials,
        google_oauth.spreadsheet_target if google_oauth.enabled else None,
        google_oauth.save_spreadsheet_target if google_oauth.enabled else None,
        output_layout_templates.latest_layout,
        google_drive.document_target if google_oauth.enabled else None,
        google_drive.create_document_spreadsheet if google_oauth.enabled else None,
        google_drive.spreadsheet_targets if google_oauth.enabled else None,
        (
            lambda document_id, status, error: google_drive.mark_document_sync(
                document_id,
                status=status,
                error=error,
            )
        )
        if google_oauth.enabled
        else None,
        subject_provider=google_oauth.active_subject if google_oauth.enabled else None,
    )
    artifact_recovery = ArtifactRecoveryService(
        settings=settings,
        database=database,
        storage=storage,
    )
    ocr_result_service = OcrResultService(
        database,
        storage,
        google_sheets,
        google_drive if google_oauth.enabled else None,
    )
    workspace_cloud_sync = WorkspaceCloudSyncService(
        database,
        storage,
        app.state.portable_backup_service,
        app.state.portable_restore_service,
        google_drive,
        google_oauth.active_subject,
    )
    google_sheets_edit_worker = GoogleSheetsEditWorker(
        google_sheets,
        ocr_result_service,
        poll_interval_seconds=settings.google_sheets.edit_poll_interval_seconds,
        mutation_notifier=workspace_cloud_sync.mark_dirty,
    )
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
            JobPageService(database, storage, settings), extraction_service, ocr_workflow_service,
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
    )
    await runner.start()
    try:
        await google_sheets_edit_worker.start()
    except Exception:
        await runner.stop()
        raise

    app.state.job_runner = runner
    app.state.job_service = JobService(
        settings=settings,
        database=database,
        storage=storage,
        runner=runner,
    )
    app.state.google_sheets = google_sheets
    app.state.google_drive = google_drive
    app.state.template_package_drive_sync = TemplatePackageDriveSyncService(
        database,
        google_drive,
    )
    app.state.output_layout_templates = output_layout_templates
    app.state.artifact_recovery = artifact_recovery
    app.state.google_oauth = google_oauth
    app.state.ocr_result_service = ocr_result_service
    app.state.google_sheets_edit_worker = google_sheets_edit_worker
    app.state.individual_record_service = IndividualRecordService(database)
    app.state.data_search_service = DataSearchService(database, storage)
    app.state.document_library_service = DocumentLibraryService(
        database,
        storage,
        google_drive if google_oauth.enabled else None,
    )
    app.state.workspace_cloud_sync_service = workspace_cloud_sync
    await workspace_cloud_sync.start()
    app.state.job_worker_status = "running"


async def stop_runtime_services(app: FastAPI) -> None:
    app.state.job_worker_status = "not_started"
    workspace_cloud_sync = getattr(app.state, "workspace_cloud_sync_service", None)
    edit_worker = getattr(app.state, "google_sheets_edit_worker", None)
    runner = getattr(app.state, "job_runner", None)
    if workspace_cloud_sync is not None:
        await workspace_cloud_sync.stop()
    if edit_worker is not None:
        await edit_worker.stop()
    if runner is not None:
        await runner.stop()


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    configure_logging(settings.logging)
    database = Database(settings.database)
    database.check_connection()
    app.state.storage = storage
    app.state.database = database
    portable_backup = PortableBackupService(database, storage)
    app.state.portable_backup_service = portable_backup
    app.state.portable_restore_service = PortableRestoreService(
        database,
        storage,
        portable_backup,
    )
    app.state.template_package_operations = TemplatePackageOperationService(
        database,
        storage,
    )
    await start_runtime_services(app)
    try:
        yield
    finally:
        await stop_runtime_services(app)
        app.state.template_package_operations.close()
        database.dispose()
