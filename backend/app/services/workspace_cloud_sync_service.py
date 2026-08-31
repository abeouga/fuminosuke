from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import socket
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import AppError
from app.db.models import WorkspaceCloudSyncState
from app.db.session import Database
from app.services.google_drive_service import GoogleDriveService
from app.services.portable_backup_service import PortableBackupService
from app.services.portable_restore_service import (
    PortableRestoreProgress,
    PortableRestoreService,
)
from app.storage.local_storage import LocalStorageService


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    file_id: str
    file_name: str
    revision: int
    sha256: str
    device_id: str
    created_at: datetime | None
    file_bytes: int


class WorkspaceCloudSyncService:
    """Synchronize complete, validated workspace snapshots through Google Drive."""

    DEBOUNCE_SECONDS = 60
    POLL_SECONDS = 30
    LEASE_SECONDS = 120
    RETAIN_SNAPSHOTS = 5
    STATE_ID = 1

    def __init__(
        self,
        database: Database,
        storage: LocalStorageService,
        portable_backup: PortableBackupService,
        portable_restore: PortableRestoreService,
        google_drive: GoogleDriveService,
        subject_provider: Callable[[], str | None],
    ) -> None:
        self._database = database
        self._storage = storage
        self._portable_backup = portable_backup
        self._portable_restore = portable_restore
        self._google_drive = google_drive
        self._subject_provider = subject_provider
        self._device_id, self._device_name = self._load_device_identity()
        self._operation_lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="workspace-cloud-sync")
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        task = self._task
        self._task = None
        if task is not None:
            await task
        if self._enabled() and self._subject_provider():
            with suppress(Exception):
                await asyncio.to_thread(
                    self._google_drive.release_workspace_lease,
                    self._device_id,
                )

    def status(self) -> dict[str, Any]:
        with self._database.session() as session:
            state = self._state(session)
            return self._state_view(state)

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not enabled:
            subject = self._subject_provider()
            if subject:
                with suppress(Exception):
                    self._google_drive.release_workspace_lease(
                        self._device_id,
                        expected_subject=subject,
                    )
            with self._database.session() as session:
                state = self._state(session)
                state.enabled = False
                state.status = "DISABLED"
                state.stage = "IDLE"
                state.progress_percent = 0
                state.message = "Google Drive自動同期は停止しています。"
                state.lease_owner_device_id = None
                state.lease_owner_device_name = None
                state.lease_expires_at = None
                state.error = None
                session.commit()
                return self._state_view(state)

        subject = self._subject_provider()
        if not subject:
            raise AppError(
                status=401,
                code="GOOGLE_ACCOUNT_REQUIRED",
                title="Google account is required",
                detail="Google Drive自動同期を有効にするにはGoogle連携が必要です。",
            )
        with self._database.session() as session:
            state = self._state(session)
            state.enabled = True
            state.google_subject = subject
            state.status = "CHECKING"
            state.stage = "CHECKING_REMOTE"
            state.progress_percent = 5
            state.message = "Google Drive上の同期状態を確認しています。"
            state.error = None
            session.commit()
        self.refresh_remote(acquire_lease=True)
        self.mark_dirty()
        self._notify()
        return self.status()

    def mark_dirty(self) -> None:
        now = datetime.now(UTC)
        with self._database.session() as session:
            state = self._state(session)
            if not state.enabled:
                return
            state.dirty = True
            state.last_change_at = now
            if state.status not in {
                "READ_ONLY",
                "RESTORE_AVAILABLE",
                "REAUTHORIZATION_REQUIRED",
                "ERROR",
            }:
                state.status = "PENDING"
                state.stage = "WAITING_FOR_IDLE"
                state.progress_percent = 0
                state.message = "変更を検出しました。操作完了後に自動同期します。"
            session.commit()
        self._notify()

    def writes_allowed(self) -> tuple[bool, str]:
        with self._database.session() as session:
            state = self._state(session)
            if not state.enabled:
                return True, ""
            blocked = state.status in {
                "READ_ONLY",
                "RESTORE_AVAILABLE",
                "REAUTHORIZATION_REQUIRED",
                "ERROR",
                "CHECKING",
            }
            return (not blocked, state.message or state.error or "同期状態を確認してください。")

    def refresh_remote(self, *, acquire_lease: bool = True) -> dict[str, Any]:
        if not self._enabled():
            return self.status()
        subject = self._subject_provider()
        if not subject:
            self._set_problem(
                "REAUTHORIZATION_REQUIRED",
                "Google連携をやり直すまで、この端末では保存・編集できません。",
                "Googleアカウントが接続されていません。",
            )
            return self.status()
        try:
            remote = self._google_drive.workspace_remote_state(
                device_id=self._device_id,
                device_name=self._device_name,
                acquire_lease=False,
                lease_seconds=self.LEASE_SECONDS,
                expected_subject=subject,
            )
            snapshots = self._snapshot_views(remote.get("snapshots"))
            latest_revision = snapshots[0].revision if snapshots else 0
            with self._database.session() as session:
                state = self._state(session)
                local_revision = state.remote_revision
            if latest_revision > local_revision:
                latest = snapshots[0]
                with self._database.session() as session:
                    state = self._state(session)
                    state.google_subject = subject
                    state.status = "RESTORE_AVAILABLE"
                    state.stage = "REMOTE_NEWER"
                    state.progress_percent = 0
                    state.message = (
                        "Google Driveにこの端末より新しい保存データがあります。"
                        "履歴を確認して取り込んでください。"
                    )
                    state.latest_snapshot_file_id = latest.file_id
                    state.latest_snapshot_sha256 = latest.sha256
                    state.latest_snapshot_created_at = latest.created_at
                    state.lease_owner_device_id = remote.get("leaseOwnerDeviceId")
                    state.lease_owner_device_name = remote.get("leaseOwnerDeviceName")
                    state.lease_expires_at = remote.get("leaseExpiresAt")
                    state.error = None
                    session.commit()
                return self.status()
            if acquire_lease:
                remote = self._google_drive.workspace_remote_state(
                    device_id=self._device_id,
                    device_name=self._device_name,
                    acquire_lease=True,
                    lease_seconds=self.LEASE_SECONDS,
                    expected_subject=subject,
                )
            lease_owned = bool(remote.get("leaseOwned"))
            with self._database.session() as session:
                state = self._state(session)
                state.google_subject = subject
                state.lease_owner_device_id = remote.get("leaseOwnerDeviceId")
                state.lease_owner_device_name = remote.get("leaseOwnerDeviceName")
                state.lease_expires_at = remote.get("leaseExpiresAt")
                if not lease_owned:
                    state.status = "READ_ONLY"
                    state.stage = "LEASE_HELD"
                    state.progress_percent = 0
                    owner = state.lease_owner_device_name or "別のPC"
                    state.message = f"{owner} が使用中のため、この端末は読み取り専用です。"
                elif state.dirty:
                    state.status = "PENDING"
                    state.stage = "WAITING_FOR_IDLE"
                    state.progress_percent = 0
                    state.message = "未同期の変更があります。自動同期を待っています。"
                else:
                    state.status = "SYNCED"
                    state.stage = "COMPLETED"
                    state.progress_percent = 100
                    state.message = "Google Driveと同期済みです。"
                state.error = None
                session.commit()
            return self.status()
        except AppError as exc:
            status = (
                "REAUTHORIZATION_REQUIRED"
                if exc.status == 401
                else "ERROR"
            )
            self._set_problem(status, exc.detail, exc.detail)
            return self.status()
        except Exception:
            self._set_problem(
                "ERROR",
                "Google Driveの同期状態を確認できないため、保存・編集を停止しています。",
                "Google Driveの同期状態を確認できませんでした。",
            )
            return self.status()

    def sync_now(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise AppError(
                status=409,
                code="WORKSPACE_SYNC_RUNNING",
                title="Workspace sync is already running",
                detail="Google Drive自動同期を処理中です。",
            )
        backup_id: str | None = None
        verify_path: Path | None = None
        old_revision = 0
        try:
            if not self._enabled():
                raise AppError(
                    status=409,
                    code="WORKSPACE_SYNC_DISABLED",
                    title="Workspace sync is disabled",
                    detail="先にGoogle Drive自動同期を有効にしてください。",
                )
            self.refresh_remote(acquire_lease=True)
            allowed, reason = self.writes_allowed()
            if not allowed:
                raise AppError(
                    status=423,
                    code="WORKSPACE_READ_ONLY",
                    title="Workspace is read only",
                    detail=reason,
                )
            subject = self._subject_provider()
            if not subject:
                raise AppError(
                    status=401,
                    code="GOOGLE_ACCOUNT_REQUIRED",
                    title="Google account is required",
                    detail="Googleアカウントを接続してください。",
                )
            remote = self._google_drive.workspace_remote_state(
                device_id=self._device_id,
                device_name=self._device_name,
                acquire_lease=True,
                lease_seconds=self.LEASE_SECONDS,
                expected_subject=subject,
            )
            snapshots = self._snapshot_views(remote.get("snapshots"))
            max_revision = max((item.revision for item in snapshots), default=0)
            with self._database.session() as session:
                state = self._state(session)
                old_revision = state.remote_revision
                next_revision = max(old_revision, max_revision) + 1
                state.remote_revision = next_revision
                state.dirty = False
                state.status = "SYNCING"
                state.stage = "CREATING_BACKUP"
                state.progress_percent = 10
                state.message = "同期用の完全バックアップを作成しています。"
                state.last_attempt_at = datetime.now(UTC)
                state.error = None
                session.commit()
            backup = self._portable_backup.create()
            backup_id = backup.backup_id
            sha256 = self._sha256(backup.path)
            self._update_progress(
                "UPLOADING",
                55,
                "Google Driveへ同期用バックアップを書き込んでいます。",
            )
            uploaded = self._google_drive.upload_workspace_snapshot(
                archive_path=backup.path,
                revision=next_revision,
                sha256=sha256,
                device_id=self._device_id,
                created_at=backup.created_at,
                expected_subject=subject,
            )
            self._update_progress(
                "VERIFYING",
                82,
                "Google Drive上のバックアップを再取得して検証しています。",
            )
            verify_path = self._storage.resolve(
                f"tmp/workspace-sync/{uuid4()}.zip"
            )
            self._google_drive.download_workspace_snapshot(
                str(uploaded["fileId"]),
                verify_path,
                expected_subject=subject,
            )
            if self._sha256(verify_path) != sha256:
                raise AppError(
                    status=409,
                    code="WORKSPACE_SNAPSHOT_HASH_MISMATCH",
                    title="Workspace snapshot hash mismatch",
                    detail="Google Driveへ保存したバックアップの検証値が一致しません。",
                )
            self._google_drive.trim_workspace_snapshots(
                retain=self.RETAIN_SNAPSHOTS,
                expected_subject=subject,
            )
            now = datetime.now(UTC)
            with self._database.session() as session:
                state = self._state(session)
                state.status = "SYNCED"
                state.stage = "COMPLETED"
                state.progress_percent = 100
                state.message = "Google Driveと同期済みです。"
                state.latest_snapshot_file_id = str(uploaded["fileId"])
                state.latest_snapshot_sha256 = sha256
                state.latest_snapshot_created_at = self._parse_datetime(
                    uploaded.get("createdAt")
                ) or backup.created_at
                state.last_success_at = now
                state.error = None
                session.commit()
            return self.status()
        except AppError as exc:
            with self._database.session() as session:
                state = self._state(session)
                state.remote_revision = old_revision
                state.dirty = True
                state.status = (
                    "PENDING"
                    if exc.code.startswith("BACKUP_")
                    else "REAUTHORIZATION_REQUIRED"
                    if exc.status == 401
                    else "ERROR"
                )
                state.stage = "WAITING_FOR_IDLE" if state.status == "PENDING" else "FAILED"
                state.progress_percent = 0
                state.message = exc.detail
                state.error = exc.detail
                session.commit()
            raise
        finally:
            if verify_path is not None:
                verify_path.unlink(missing_ok=True)
            if backup_id is not None:
                with suppress(Exception):
                    self._portable_backup.delete(backup_id)
            self._operation_lock.release()

    def snapshots(self) -> list[WorkspaceSnapshot]:
        subject = self._subject_provider()
        if not subject:
            raise AppError(
                status=401,
                code="GOOGLE_ACCOUNT_REQUIRED",
                title="Google account is required",
                detail="Googleアカウントを接続してください。",
            )
        remote = self._google_drive.workspace_remote_state(
            device_id=self._device_id,
            device_name=self._device_name,
            acquire_lease=False,
            lease_seconds=self.LEASE_SECONDS,
            expected_subject=subject,
        )
        return self._snapshot_views(remote.get("snapshots"))

    def prepare_restore(
        self,
        file_id: str,
        operation_id: str,
    ) -> PortableRestoreProgress:
        try:
            normalized_operation_id = str(UUID(operation_id))
        except ValueError as exc:
            raise AppError(
                status=422,
                code="RESTORE_OPERATION_ID_INVALID",
                title="Invalid restore operation id",
                detail="取り込み処理IDが正しくありません。",
            ) from exc
        subject = self._subject_provider()
        if not subject:
            raise AppError(
                status=401,
                code="GOOGLE_ACCOUNT_REQUIRED",
                title="Google account is required",
                detail="Googleアカウントを接続してください。",
            )
        snapshots = {item.file_id: item for item in self.snapshots()}
        selected = snapshots.get(file_id)
        if selected is None:
            raise AppError(
                status=404,
                code="WORKSPACE_SNAPSHOT_NOT_FOUND",
                title="Workspace snapshot was not found",
                detail="選択したGoogle Drive同期履歴が見つかりません。",
            )
        target = self._storage.resolve(
            f"tmp/workspace-sync/{normalized_operation_id}.zip"
        )
        try:
            self._google_drive.download_workspace_snapshot(
                file_id,
                target,
                expected_subject=subject,
            )
            if self._sha256(target) != selected.sha256:
                raise AppError(
                    status=409,
                    code="WORKSPACE_SNAPSHOT_HASH_MISMATCH",
                    title="Workspace snapshot hash mismatch",
                    detail="Google Driveから取得したバックアップの検証値が一致しません。",
                )
            with target.open("rb") as source:
                return self._portable_restore.receive_and_validate(
                    normalized_operation_id,
                    selected.file_name,
                    source,
                )
        finally:
            target.unlink(missing_ok=True)

    async def _run(self) -> None:
        while not self._stopping:
            wake = self._wake
            if wake is None:
                return
            with suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=self.POLL_SECONDS)
            wake.clear()
            if self._stopping or not self._enabled():
                continue
            await asyncio.to_thread(self._tick)

    def _tick(self) -> None:
        self.refresh_remote(acquire_lease=True)
        with self._database.session() as session:
            state = self._state(session)
            changed_at = self._aware(state.last_change_at)
            ready = bool(
                state.dirty
                and changed_at
                and (datetime.now(UTC) - changed_at).total_seconds()
                >= self.DEBOUNCE_SECONDS
            )
            status = state.status
        if ready and status == "PENDING":
            try:
                self.sync_now()
            except AppError:
                return

    def _enabled(self) -> bool:
        with self._database.session() as session:
            return self._state(session).enabled

    def _state(self, session: Any) -> WorkspaceCloudSyncState:
        state = session.get(WorkspaceCloudSyncState, self.STATE_ID)
        if state is None:
            state = WorkspaceCloudSyncState(
                id=self.STATE_ID,
                enabled=False,
                status="DISABLED",
                stage="IDLE",
                progress_percent=0,
                message="Google Drive自動同期は停止しています。",
                dirty=False,
                remote_revision=0,
            )
            session.add(state)
            session.commit()
        return state

    def _state_view(self, state: WorkspaceCloudSyncState) -> dict[str, Any]:
        return {
            "enabled": state.enabled,
            "status": state.status,
            "stage": state.stage,
            "progressPercent": state.progress_percent,
            "message": state.message,
            "dirty": state.dirty,
            "remoteRevision": state.remote_revision,
            "latestSnapshotFileId": state.latest_snapshot_file_id,
            "latestSnapshotSha256": state.latest_snapshot_sha256,
            "latestSnapshotCreatedAt": self._aware(state.latest_snapshot_created_at),
            "leaseOwnerDeviceId": state.lease_owner_device_id,
            "leaseOwnerDeviceName": state.lease_owner_device_name,
            "leaseExpiresAt": self._aware(state.lease_expires_at),
            "lastAttemptAt": self._aware(state.last_attempt_at),
            "lastSuccessAt": self._aware(state.last_success_at),
            "error": state.error,
            "deviceId": self._device_id,
            "deviceName": self._device_name,
            "readOnly": state.enabled
            and state.status
            in {
                "READ_ONLY",
                "RESTORE_AVAILABLE",
                "REAUTHORIZATION_REQUIRED",
                "ERROR",
                "CHECKING",
            },
        }

    def _set_problem(self, status: str, message: str, error: str) -> None:
        with self._database.session() as session:
            state = self._state(session)
            state.status = status
            state.stage = "FAILED"
            state.progress_percent = 0
            state.message = message
            state.error = error
            session.commit()

    def _update_progress(self, stage: str, progress: int, message: str) -> None:
        with self._database.session() as session:
            state = self._state(session)
            state.status = "SYNCING"
            state.stage = stage
            state.progress_percent = progress
            state.message = message
            session.commit()

    def _load_device_identity(self) -> tuple[str, str]:
        path = self._storage.resolve("local/workspace-device.json")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            device_id = str(payload["deviceId"])
            UUID(device_id)
            device_name = str(payload.get("deviceName") or "")
            if device_name:
                return device_id, device_name[:120]
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
        device_id = str(uuid4())
        device_name = (socket.gethostname() or platform.node() or "このPC")[:120]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"deviceId": device_id, "deviceName": device_name},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return device_id, device_name

    @classmethod
    def _snapshot_views(cls, value: object) -> list[WorkspaceSnapshot]:
        if not isinstance(value, list):
            return []
        result: list[WorkspaceSnapshot] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            result.append(
                WorkspaceSnapshot(
                    file_id=str(item.get("fileId") or ""),
                    file_name=str(item.get("fileName") or ""),
                    revision=int(item.get("revision") or 0),
                    sha256=str(item.get("sha256") or ""),
                    device_id=str(item.get("deviceId") or ""),
                    created_at=cls._parse_datetime(item.get("createdAt")),
                    file_bytes=int(item.get("fileBytes") or 0),
                )
            )
        return result

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return WorkspaceCloudSyncService._aware(value)
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _notify(self) -> None:
        if self._wake is not None:
            self._wake.set()
