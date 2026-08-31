from collections.abc import Iterator
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import Database
from app.services.google_sheets_sync_service import GoogleSheetsSyncService
from app.services.job_service import JobService
from app.services.ocr_result_service import OcrResultService
from app.services.output_layout_template_service import OutputLayoutTemplateService
from app.services.portable_backup_service import PortableBackupService
from app.services.portable_restore_service import PortableRestoreService
from app.storage.local_storage import LocalStorageService


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def get_storage(request: Request) -> LocalStorageService:
    return cast(LocalStorageService, request.app.state.storage)


def get_job_service(request: Request) -> JobService:
    return cast(JobService, request.app.state.job_service)


def get_ocr_result_service(request: Request) -> OcrResultService:
    return cast(OcrResultService, request.app.state.ocr_result_service)


def get_google_sheets(request: Request) -> GoogleSheetsSyncService:
    return cast(GoogleSheetsSyncService, request.app.state.google_sheets)


def get_output_layout_templates(request: Request) -> OutputLayoutTemplateService:
    return cast(
        OutputLayoutTemplateService,
        request.app.state.output_layout_templates,
    )


def get_portable_backup_service(request: Request) -> PortableBackupService:
    return cast(PortableBackupService, request.app.state.portable_backup_service)


def get_portable_restore_service(request: Request) -> PortableRestoreService:
    return cast(PortableRestoreService, request.app.state.portable_restore_service)


def get_session(request: Request) -> Iterator[Session]:
    database = get_database(request)
    with database.session() as session:
        yield session
