from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_portable_backup_service
from app.core.errors import ApiModel
from app.services.portable_backup_service import PortableBackup, PortableBackupService

router = APIRouter(prefix="/system-backups", tags=["system-backups"])


class PortableBackupExport(ApiModel):
    id: str
    file_name: str
    file_bytes: int
    file_count: int
    source_bytes: int
    created_at: datetime
    expires_at: datetime
    download_url: str


@router.post(
    "/portable",
    response_model=PortableBackupExport,
    response_model_by_alias=True,
)
def create_portable_backup(
    service: Annotated[PortableBackupService, Depends(get_portable_backup_service)],
) -> PortableBackupExport:
    backup = service.create()
    return _export(backup)


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
