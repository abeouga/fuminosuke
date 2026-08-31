from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.models import WorkspaceCloudSyncState
from app.db.session import Database
from app.services.portable_backup_service import PortableBackupService
from app.services.portable_restore_service import PortableRestoreService
from app.services.workspace_cloud_sync_service import WorkspaceCloudSyncService
from app.storage.local_storage import LocalStorageService


class FakeWorkspaceDrive:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self.contents: dict[str, bytes] = {}
        self.owner_id: str | None = None
        self.owner_name: str | None = None
        self.lease_expires_at: datetime | None = None

    def workspace_remote_state(
        self,
        *,
        device_id: str,
        device_name: str,
        acquire_lease: bool,
        **_kwargs: object,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        active = bool(self.lease_expires_at and self.lease_expires_at > now)
        if acquire_lease and (not active or self.owner_id == device_id):
            self.owner_id = device_id
            self.owner_name = device_name
            self.lease_expires_at = now + timedelta(minutes=2)
        return {
            "googleSubject": "subject-1",
            "rootFolderId": "workspace-root",
            "leaseOwnerDeviceId": self.owner_id,
            "leaseOwnerDeviceName": self.owner_name,
            "leaseExpiresAt": self.lease_expires_at,
            "leaseOwned": self.owner_id == device_id,
            "snapshots": list(self.snapshots),
        }

    def upload_workspace_snapshot(
        self,
        *,
        archive_path: Path,
        revision: int,
        sha256: str,
        device_id: str,
        created_at: datetime,
        **_kwargs: object,
    ) -> dict[str, Any]:
        file_id = f"snapshot-{revision}"
        item = {
            "fileId": file_id,
            "fileName": f"workspace-{revision}.zip",
            "revision": revision,
            "sha256": sha256,
            "deviceId": device_id,
            "createdAt": created_at.isoformat(),
            "fileBytes": archive_path.stat().st_size,
        }
        self.snapshots.insert(0, item)
        self.contents[file_id] = archive_path.read_bytes()
        return item

    def download_workspace_snapshot(
        self,
        file_id: str,
        destination: Path,
        **_kwargs: object,
    ) -> dict[str, Any]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.contents[file_id])
        return next(item for item in self.snapshots if item["fileId"] == file_id)

    def trim_workspace_snapshots(self, *, retain: int, **_kwargs: object) -> None:
        removed = self.snapshots[retain:]
        self.snapshots = self.snapshots[:retain]
        for item in removed:
            self.contents.pop(str(item["fileId"]), None)

    def release_workspace_lease(self, device_id: str, **_kwargs: object) -> None:
        if self.owner_id == device_id:
            self.owner_id = None
            self.owner_name = None
            self.lease_expires_at = None


def _service(tmp_path: Path) -> tuple[WorkspaceCloudSyncService, FakeWorkspaceDrive, Database]:
    storage = LocalStorageService(tmp_path / "data")
    storage.initialize()
    settings = type(
        "DatabaseConfig",
        (),
        {"url": f"sqlite:///{(storage.root / 'app.db').as_posix()}"},
    )()
    database = Database(settings)  # type: ignore[arg-type]
    Base.metadata.create_all(database.engine)
    backup = PortableBackupService(database, storage)
    restore = PortableRestoreService(database, storage, backup)
    drive = FakeWorkspaceDrive()
    service = WorkspaceCloudSyncService(
        database,
        storage,
        backup,
        restore,
        drive,  # type: ignore[arg-type]
        lambda: "subject-1",
    )
    return service, drive, database


def test_complete_workspace_snapshot_is_uploaded_and_verified(tmp_path: Path) -> None:
    service, drive, database = _service(tmp_path)
    try:
        enabled = service.set_enabled(True)
        assert enabled["enabled"] is True
        assert enabled["dirty"] is True

        result = service.sync_now()

        assert result["status"] == "SYNCED"
        assert result["remoteRevision"] == 1
        assert result["dirty"] is False
        assert len(drive.snapshots) == 1
        assert len(drive.contents["snapshot-1"]) > 0
        with database.session() as session:
            state = session.get(WorkspaceCloudSyncState, 1)
            assert state is not None
            assert state.latest_snapshot_sha256 == drive.snapshots[0]["sha256"]
    finally:
        database.dispose()


def test_newer_remote_snapshot_blocks_local_writes(tmp_path: Path) -> None:
    service, drive, database = _service(tmp_path)
    try:
        drive.snapshots.append(
            {
                "fileId": "snapshot-4",
                "fileName": "workspace-4.zip",
                "revision": 4,
                "sha256": "a" * 64,
                "deviceId": "other-device",
                "createdAt": datetime.now(UTC).isoformat(),
                "fileBytes": 123,
            }
        )

        result = service.set_enabled(True)

        assert result["status"] == "RESTORE_AVAILABLE"
        assert result["readOnly"] is True
        allowed, _reason = service.writes_allowed()
        assert allowed is False
    finally:
        database.dispose()


def test_workspace_sync_status_endpoint_is_available_when_disabled(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/workspace-sync")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "status": "DISABLED",
        "stage": "IDLE",
        "progressPercent": 0,
        "message": "Google Drive自動同期は停止しています。",
        "dirty": False,
        "remoteRevision": 0,
        "latestSnapshotFileId": None,
        "latestSnapshotSha256": None,
        "latestSnapshotCreatedAt": None,
        "leaseOwnerDeviceId": None,
        "leaseOwnerDeviceName": None,
        "leaseExpiresAt": None,
        "lastAttemptAt": None,
        "lastSuccessAt": None,
        "error": None,
        "deviceId": response.json()["deviceId"],
        "deviceName": response.json()["deviceName"],
        "readOnly": False,
    }


def test_remote_writer_lease_blocks_local_mutations(client: TestClient) -> None:
    database = client.app.state.database
    with database.session() as session:
        state = session.get(WorkspaceCloudSyncState, 1)
        assert state is not None
        state.enabled = True
        state.status = "READ_ONLY"
        state.message = "別のPCが使用中です。"
        session.commit()

    response = client.post("/api/v1/sire-names", json={"name": "勝忠平"})

    assert response.status_code == 423
    assert response.json()["code"] == "WORKSPACE_READ_ONLY"
