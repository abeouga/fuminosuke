from __future__ import annotations

from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, Body, Depends, File, Request, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies import (
    get_portable_backup_service,
    get_portable_restore_service,
)
from app.core.errors import ApiModel, AppError
from app.core.lifecycle import start_runtime_services, stop_runtime_services
from app.services.portable_backup_service import (
    PortableBackup,
    PortableBackupProgress,
    PortableBackupService,
)
from app.services.portable_restore_service import (
    PortableRestoreProgress,
    PortableRestoreService,
)

router = APIRouter(prefix="/system-backups", tags=["system-backups"])


class PortableBackupCreateRequest(ApiModel):
    operation_id: str | None = None


class PortableBackupExport(ApiModel):
    id: str
    file_name: str
    file_bytes: int
    file_count: int
    source_bytes: int
    created_at: datetime
    expires_at: datetime
    download_url: str


class PortableBackupOperation(ApiModel):
    operation_id: str
    status: str
    stage: str
    progress_percent: int
    message: str
    bytes_processed: int
    total_bytes: int | None
    backup_id: str | None
    error_code: str | None
    error_message: str | None
    updated_at: datetime


class PortableRestoreOperation(ApiModel):
    operation_id: str
    status: str
    stage: str
    progress_percent: int
    message: str
    file_name: str
    file_bytes: int
    bytes_processed: int
    total_bytes: int | None
    file_count: int | None
    source_bytes: int | None
    backup_created_at: datetime | None
    schema_revision: str | None
    error_code: str | None
    error_message: str | None
    rollback_expires_at: datetime | None
    updated_at: datetime


@router.post(
    "/portable",
    response_model=PortableBackupExport,
    response_model_by_alias=True,
)
def create_portable_backup(
    service: Annotated[
        PortableBackupService,
        Depends(get_portable_backup_service),
    ],
    payload: Annotated[PortableBackupCreateRequest | None, Body()] = None,
) -> PortableBackupExport:
    backup = service.create(payload.operation_id if payload else None)
    return _export(backup)


@router.get(
    "/portable-operations/{operation_id}",
    response_model=PortableBackupOperation,
    response_model_by_alias=True,
)
def portable_backup_operation(
    operation_id: str,
    service: Annotated[PortableBackupService, Depends(get_portable_backup_service)],
) -> PortableBackupOperation:
    return _backup_operation(service.operation(operation_id))


@router.get("/portable/{backup_id}", response_class=FileResponse)
def download_portable_backup(
    backup_id: str,
    service: Annotated[PortableBackupService, Depends(get_portable_backup_service)],
) -> FileResponse:
    backup = service.get(backup_id)
    return FileResponse(
        path=backup.path,
        media_type="application/zip",
        filename=backup.filename,
        headers={
            "Cache-Control": "no-store",
            "X-Backup-Id": backup.backup_id,
            "X-Backup-File-Count": str(backup.file_count),
            "X-Backup-Source-Bytes": str(backup.source_bytes),
        },
    )


@router.delete(
    "/portable/{backup_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_portable_backup(
    backup_id: str,
    service: Annotated[PortableBackupService, Depends(get_portable_backup_service)],
) -> Response:
    service.delete(backup_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/portable-imports/{operation_id}",
    response_model=PortableRestoreOperation,
    response_model_by_alias=True,
)
def validate_portable_import(
    operation_id: str,
    file: Annotated[UploadFile, File()],
    service: Annotated[PortableRestoreService, Depends(get_portable_restore_service)],
) -> PortableRestoreOperation:
    progress = service.receive_and_validate(
        operation_id,
        file.filename or "portable-backup.zip",
        file.file,
    )
    return _restore_operation(progress)


@router.get(
    "/portable-imports/{operation_id}",
    response_model=PortableRestoreOperation,
    response_model_by_alias=True,
)
def portable_import_operation(
    operation_id: str,
    service: Annotated[PortableRestoreService, Depends(get_portable_restore_service)],
) -> PortableRestoreOperation:
    return _restore_operation(service.status(operation_id))


@router.post(
    "/portable-imports/{operation_id}/apply",
    response_model=PortableRestoreOperation,
    response_model_by_alias=True,
)
async def apply_portable_import(
    operation_id: str,
    request: Request,
    service: Annotated[PortableRestoreService, Depends(get_portable_restore_service)],
) -> PortableRestoreOperation:
    service.begin_apply(operation_id)
    runtime_stopped = False
    applied = False
    try:
        await stop_runtime_services(request.app)
        runtime_stopped = True
        await anyio.to_thread.run_sync(service.apply_filesystem, operation_id)
        applied = True
        try:
            await start_runtime_services(request.app)
        except Exception as exc:
            await anyio.to_thread.run_sync(service.rollback_applied, operation_id)
            applied = False
            try:
                await start_runtime_services(request.app)
            except Exception as recovery_exc:
                raise AppError(
                    status=500,
                    code="RESTORE_RUNTIME_RECOVERY_FAILED",
                    title="Restore runtime recovery failed",
                    detail=(
                        "元データへ戻しましたが、サービスを再開できませんでした。"
                        "システムを再起動してください。"
                    ),
                ) from recovery_exc
            raise AppError(
                status=500,
                code="RESTORE_RUNTIME_START_FAILED",
                title="Restore runtime start failed",
                detail="取り込み後の初期化に失敗したため、元データへ戻しました。",
            ) from exc
        return _restore_operation(service.complete_apply(operation_id))
    except AppError as exc:
        if runtime_stopped and not request.app.state.job_runner.is_running:
            if applied:
                await anyio.to_thread.run_sync(service.rollback_applied, operation_id)
            with suppress(Exception):
                await start_runtime_services(request.app)
        service.fail_apply(operation_id, exc)
        raise
    finally:
        service.end_apply()


@router.delete(
    "/portable-imports/{operation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_portable_import(
    operation_id: str,
    service: Annotated[PortableRestoreService, Depends(get_portable_restore_service)],
) -> Response:
    service.delete(operation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _export(backup: PortableBackup) -> PortableBackupExport:
    return PortableBackupExport(
        id=backup.backup_id,
        file_name=backup.filename,
        file_bytes=backup.file_bytes,
        file_count=backup.file_count,
        source_bytes=backup.source_bytes,
        created_at=backup.created_at,
        expires_at=backup.expires_at,
        download_url=f"/api/v1/system-backups/portable/{backup.backup_id}",
    )


def _backup_operation(progress: PortableBackupProgress) -> PortableBackupOperation:
    return PortableBackupOperation(**asdict(progress))


def _restore_operation(progress: PortableRestoreProgress) -> PortableRestoreOperation:
    return PortableRestoreOperation(**asdict(progress))
