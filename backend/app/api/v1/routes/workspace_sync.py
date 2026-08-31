from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends

from app.api.dependencies import get_workspace_cloud_sync_service
from app.api.v1.routes.system_backups import (
    PortableRestoreOperation,
    _restore_operation,
)
from app.core.errors import ApiModel
from app.services.workspace_cloud_sync_service import WorkspaceCloudSyncService

router = APIRouter(prefix="/workspace-sync", tags=["workspace-sync"])


class WorkspaceSyncSetting(ApiModel):
    enabled: bool


class WorkspaceSyncStatus(ApiModel):
    enabled: bool
    status: str
    stage: str
    progress_percent: int
    message: str
    dirty: bool
    remote_revision: int
    latest_snapshot_file_id: str | None
    latest_snapshot_sha256: str | None
    latest_snapshot_created_at: datetime | None
    lease_owner_device_id: str | None
    lease_owner_device_name: str | None
    lease_expires_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    error: str | None
    device_id: str
    device_name: str
    read_only: bool


class WorkspaceSnapshotView(ApiModel):
    file_id: str
    file_name: str
    revision: int
    sha256: str
    device_id: str
    created_at: datetime | None
    file_bytes: int


class WorkspaceSnapshotList(ApiModel):
    items: list[WorkspaceSnapshotView]


class WorkspaceRestoreRequest(ApiModel):
    operation_id: str


@router.get("", response_model=WorkspaceSyncStatus, response_model_by_alias=True)
def workspace_sync_status(
    service: Annotated[
        WorkspaceCloudSyncService,
        Depends(get_workspace_cloud_sync_service),
    ],
) -> WorkspaceSyncStatus:
    return WorkspaceSyncStatus(**service.status())


@router.put("", response_model=WorkspaceSyncStatus, response_model_by_alias=True)
def update_workspace_sync(
    payload: WorkspaceSyncSetting,
    service: Annotated[
        WorkspaceCloudSyncService,
        Depends(get_workspace_cloud_sync_service),
    ],
) -> WorkspaceSyncStatus:
    return WorkspaceSyncStatus(**service.set_enabled(payload.enabled))


@router.post("/refresh", response_model=WorkspaceSyncStatus, response_model_by_alias=True)
def refresh_workspace_sync(
    service: Annotated[
        WorkspaceCloudSyncService,
        Depends(get_workspace_cloud_sync_service),
    ],
) -> WorkspaceSyncStatus:
    return WorkspaceSyncStatus(**service.refresh_remote(acquire_lease=True))


@router.post("/sync-now", response_model=WorkspaceSyncStatus, response_model_by_alias=True)
async def sync_workspace_now(
    service: Annotated[
        WorkspaceCloudSyncService,
        Depends(get_workspace_cloud_sync_service),
    ],
) -> WorkspaceSyncStatus:
    result = await anyio.to_thread.run_sync(service.sync_now)
    return WorkspaceSyncStatus(**result)


@router.get(
    "/snapshots",
    response_model=WorkspaceSnapshotList,
    response_model_by_alias=True,
)
def workspace_snapshots(
    service: Annotated[
        WorkspaceCloudSyncService,
        Depends(get_workspace_cloud_sync_service),
    ],
) -> WorkspaceSnapshotList:
    return WorkspaceSnapshotList(
        items=[WorkspaceSnapshotView(**asdict(item)) for item in service.snapshots()]
    )


@router.post(
    "/snapshots/{file_id}/prepare-restore",
    response_model=PortableRestoreOperation,
    response_model_by_alias=True,
)
async def prepare_workspace_restore(
    file_id: str,
    payload: WorkspaceRestoreRequest,
    service: Annotated[
        WorkspaceCloudSyncService,
        Depends(get_workspace_cloud_sync_service),
    ],
) -> PortableRestoreOperation:
    progress = await anyio.to_thread.run_sync(
        service.prepare_restore,
        file_id,
        payload.operation_id,
    )
    return _restore_operation(progress)
