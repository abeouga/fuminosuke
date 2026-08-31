from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import threading
import zipfile
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO, ClassVar, Literal, TypedDict
from uuid import UUID

from app.core.errors import AppError
from app.db.session import Database
from app.services.portable_backup_service import PortableBackupService
from app.storage.local_storage import LocalStorageService


class _ManifestEntry(TypedDict):
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ValidatedManifest:
    created_at: datetime
    schema_revision: str
    file_count: int
    source_bytes: int
    entries: tuple[_ManifestEntry, ...]


@dataclass(slots=True)
class PortableRestoreProgress:
    operation_id: str
    status: Literal["RUNNING", "READY", "APPLYING", "COMPLETED", "FAILED"]
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


@dataclass(slots=True)
class _ImportRecord:
    progress: PortableRestoreProgress
    work_dir: Path
    archive_path: Path
    staged_data: Path
    rollback_dir: Path
    manifest: _ValidatedManifest | None = None
    applied: bool = False
    swap_states: dict[str, _SwapState] | None = None


@dataclass(slots=True)
class _SwapState:
    live_moved: bool = False
    staged_installed: bool = False


class PortableRestoreService:
    """Validate and atomically apply a portable backup to the local data store."""

    ARCHIVE_ROOT = "llm-ocr-portable-backup"
    INCLUDED_DIRECTORIES = ("uploads", "artifacts", "templates", "exports")
    BUFFER_BYTES = 1024 * 1024
    MAX_ARCHIVE_BYTES = 64 * 1024 * 1024 * 1024
    MAX_SOURCE_BYTES = 64 * 1024 * 1024 * 1024
    MAX_MANIFEST_BYTES = 32 * 1024 * 1024
    MINIMUM_FREE_MARGIN_BYTES = 128 * 1024 * 1024
    STALE_FILE_AGE = timedelta(hours=24)
    _WINDOWS_RESERVED_NAMES: ClassVar[set[str]] = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }

    def __init__(
        self,
        database: Database,
        storage: LocalStorageService,
        coordinator: PortableBackupService,
    ) -> None:
        self._database = database
        self._storage = storage
        self._coordinator = coordinator
        self._state_lock = threading.Lock()
        self._records: dict[str, _ImportRecord] = {}
        self._cleanup_stale_paths(self._imports_root())
        self._cleanup_stale_paths(self._rollbacks_root())

    def receive_and_validate(
        self,
        operation_id: str,
        filename: str,
        source: BinaryIO,
    ) -> PortableRestoreProgress:
        normalized_id = self._operation_id(operation_id)
        safe_filename = Path(filename or "portable-backup.zip").name
        if not safe_filename.lower().endswith(".zip"):
            raise AppError(
                status=415,
                code="RESTORE_FILE_TYPE_INVALID",
                title="Invalid restore file type",
                detail="持ち出しバックアップのZIPファイルを選択してください。",
            )
        work_dir = self._imports_root() / normalized_id
        archive_path = work_dir / "uploaded.zip"
        staged_data = work_dir / "staged" / "data"
        rollback_dir = self._rollbacks_root() / normalized_id
        with self._state_lock:
            existing = self._records.get(normalized_id)
            if existing is not None and existing.progress.status != "FAILED":
                raise AppError(
                    status=409,
                    code="RESTORE_OPERATION_EXISTS",
                    title="Restore operation already exists",
                    detail="同じ取り込み処理が既に存在します。",
                )
            progress = PortableRestoreProgress(
                operation_id=normalized_id,
                status="RUNNING",
                stage="RECEIVING_FILE",
                progress_percent=5,
                message="選択したZIPをシステム領域へ受け付けています。",
                file_name=safe_filename,
                file_bytes=0,
                bytes_processed=0,
                total_bytes=None,
                file_count=None,
                source_bytes=None,
                backup_created_at=None,
                schema_revision=None,
                error_code=None,
                error_message=None,
                rollback_expires_at=None,
                updated_at=datetime.now(UTC),
            )
            self._records[normalized_id] = _ImportRecord(
                progress=progress,
                work_dir=work_dir,
                archive_path=archive_path,
                staged_data=staged_data,
                rollback_dir=rollback_dir,
            )

        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=False)
        try:
            file_bytes = self._copy_upload(normalized_id, source, archive_path)
            manifest = self._validate_and_extract(
                normalized_id,
                archive_path,
                staged_data,
            )
            self._validate_staged_database(staged_data / "app.db", manifest)
            for directory in self.INCLUDED_DIRECTORIES:
                (staged_data / directory).mkdir(parents=True, exist_ok=True)
            with self._state_lock:
                record = self._records[normalized_id]
                record.manifest = manifest
            self._update(
                normalized_id,
                status="READY",
                stage="READY_TO_APPLY",
                progress_percent=75,
                message="バックアップは正常です。内容を確認して取り込みを実行してください。",
                file_bytes=file_bytes,
                bytes_processed=manifest.source_bytes,
                total_bytes=manifest.source_bytes,
                file_count=manifest.file_count,
                source_bytes=manifest.source_bytes,
                backup_created_at=manifest.created_at,
                schema_revision=manifest.schema_revision,
            )
            return self.status(normalized_id)
        except AppError as exc:
            self._fail(normalized_id, exc)
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
        except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            error = AppError(
                status=422,
                code="RESTORE_ARCHIVE_INVALID",
                title="Invalid portable backup",
                detail="ZIPの構造または内容を検証できませんでした。",
            )
            self._fail(normalized_id, error)
            shutil.rmtree(work_dir, ignore_errors=True)
            raise error from exc

    def status(self, operation_id: str) -> PortableRestoreProgress:
        normalized_id = self._operation_id(operation_id)
        with self._state_lock:
            record = self._records.get(normalized_id)
            if record is None:
                raise AppError(
                    status=404,
                    code="RESTORE_OPERATION_NOT_FOUND",
                    title="Restore operation not found",
                    detail="取り込み処理が見つかりません。ZIPを選び直してください。",
                )
            return replace(record.progress)

    def begin_apply(self, operation_id: str) -> None:
        normalized_id = self._operation_id(operation_id)
        with self._state_lock:
            record = self._records.get(normalized_id)
            if record is None or record.progress.status != "READY":
                raise AppError(
                    status=409,
                    code="RESTORE_NOT_READY",
                    title="Restore is not ready",
                    detail="バックアップの検証完了後に取り込みを実行してください。",
                )
        self._coordinator.begin_restore()
        self._update(
            normalized_id,
            status="APPLYING",
            stage="STOPPING_SERVICES",
            progress_percent=78,
            message="保存処理とバックグラウンド処理を停止しています。",
        )

    def apply_filesystem(self, operation_id: str) -> None:
        normalized_id = self._operation_id(operation_id)
        record = self._record(normalized_id)
        if record.manifest is None or not (record.staged_data / "app.db").is_file():
            raise self._not_ready_error()
        self._update(
            normalized_id,
            stage="CHECKPOINTING_DATABASE",
            progress_percent=82,
            message="現在のデータベースを安全に退避しています。",
        )
        self._database.dispose()
        self._checkpoint_live_database()
        shutil.rmtree(record.rollback_dir, ignore_errors=True)
        record.rollback_dir.mkdir(parents=True, exist_ok=False)
        try:
            self._update(
                normalized_id,
                stage="SWAPPING_DATA",
                progress_percent=88,
                message="検証済みデータへ切り替えています。",
            )
            self._swap_in_staged_data(record)
            self._storage.initialize()
            self._database.check_connection()
            self._update(
                normalized_id,
                stage="RESTARTING_SERVICES",
                progress_percent=95,
                message="取り込んだデータでシステムを再初期化しています。",
            )
            record.applied = True
        except Exception as exc:
            self._rollback_filesystem(record)
            if isinstance(exc, AppError):
                raise
            raise AppError(
                status=500,
                code="RESTORE_APPLY_FAILED",
                title="Restore apply failed",
                detail="取り込みに失敗したため、元の保存データへ戻しました。",
            ) from exc

    def rollback_applied(self, operation_id: str) -> None:
        record = self._record(self._operation_id(operation_id))
        if record.applied:
            self._database.dispose()
            self._rollback_filesystem(record)
            record.applied = False

    def complete_apply(self, operation_id: str) -> PortableRestoreProgress:
        normalized_id = self._operation_id(operation_id)
        record = self._record(normalized_id)
        if not record.applied:
            raise self._not_ready_error()
        expires_at = datetime.now(UTC) + self.STALE_FILE_AGE
        self._update(
            normalized_id,
            status="COMPLETED",
            stage="COMPLETED",
            progress_percent=100,
            message="持ち出しバックアップの取り込みが完了しました。",
            rollback_expires_at=expires_at,
        )
        shutil.rmtree(record.work_dir, ignore_errors=True)
        return self.status(normalized_id)

    def fail_apply(self, operation_id: str, error: AppError) -> None:
        self._fail(self._operation_id(operation_id), error)

    def end_apply(self) -> None:
        self._coordinator.end_exclusive_operation()

    def delete(self, operation_id: str) -> None:
        normalized_id = self._operation_id(operation_id)
        with self._state_lock:
            record = self._records.get(normalized_id)
            if record is None:
                return
            if record.progress.status == "APPLYING":
                raise AppError(
                    status=409,
                    code="RESTORE_APPLYING",
                    title="Restore is applying",
                    detail="取り込み適用中は削除できません。",
                )
            self._records.pop(normalized_id, None)
        shutil.rmtree(record.work_dir, ignore_errors=True)

    def _copy_upload(self, operation_id: str, source: BinaryIO, target: Path) -> int:
        written = 0
        with target.open("xb") as destination:
            while chunk := source.read(self.BUFFER_BYTES):
                written += len(chunk)
                if written > self.MAX_ARCHIVE_BYTES:
                    raise AppError(
                        status=413,
                        code="RESTORE_ARCHIVE_TOO_LARGE",
                        title="Restore archive is too large",
                        detail="ZIPがシステムの取り込み上限を超えています。",
                    )
                destination.write(chunk)
                self._update(
                    operation_id,
                    stage="RECEIVING_FILE",
                    progress_percent=min(14, 5 + written // (128 * 1024 * 1024)),
                    message="選択したZIPをシステム領域へ受け付けています。",
                    file_bytes=written,
                    bytes_processed=written,
                )
        if written <= 0:
            raise AppError(
                status=422,
                code="RESTORE_ARCHIVE_EMPTY",
                title="Restore archive is empty",
                detail="選択したZIPにデータがありません。",
            )
        return written

    def _validate_and_extract(
        self,
        operation_id: str,
        archive_path: Path,
        staged_data: Path,
    ) -> _ValidatedManifest:
        self._update(
            operation_id,
            stage="VALIDATING_STRUCTURE",
            progress_percent=16,
            message="ZIP構造とmanifest.jsonを確認しています。",
        )
        with zipfile.ZipFile(archive_path) as archive:
            file_infos = [item for item in archive.infolist() if not item.is_dir()]
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            for info in file_infos:
                lowered = info.filename.lower()
                if lowered in info_by_name:
                    raise self._invalid_archive("ZIP内に重複パスがあります。")
                if self._zip_info_is_link(info) or info.flag_bits & 0x1:
                    raise self._invalid_archive("外部参照または暗号化ZIPは取り込めません。")
                info_by_name[lowered] = info

            manifest_name = f"{self.ARCHIVE_ROOT}/manifest.json"
            manifest_info = info_by_name.get(manifest_name.lower())
            if manifest_info is None or manifest_info.file_size > self.MAX_MANIFEST_BYTES:
                raise self._invalid_archive("manifest.jsonを確認できません。")
            manifest_raw = json.loads(archive.read(manifest_info).decode("utf-8-sig"))
            manifest = self._parse_manifest(manifest_raw, info_by_name)
            expected_files = {
                manifest_name.lower(),
                f"{self.ARCHIVE_ROOT}/復元手順.txt".lower(),
                f"{self.ARCHIVE_ROOT}/バックアップ検証.ps1".lower(),
                *(
                    f"{self.ARCHIVE_ROOT}/{entry['path']}".lower()
                    for entry in manifest.entries
                ),
            }
            unexpected = set(info_by_name) - expected_files
            if unexpected:
                raise self._invalid_archive("ZIP内に未記載ファイルがあります。")
            if shutil.disk_usage(archive_path.parent).free < (
                manifest.source_bytes + self.MINIMUM_FREE_MARGIN_BYTES
            ):
                raise AppError(
                    status=507,
                    code="RESTORE_STORAGE_INSUFFICIENT",
                    title="Insufficient restore storage",
                    detail="取り込み検証用の空き容量が不足しています。",
                )

            staged_data.mkdir(parents=True, exist_ok=False)
            processed = 0
            for entry in manifest.entries:
                info = info_by_name[f"{self.ARCHIVE_ROOT}/{entry['path']}".lower()]
                destination = self._staged_destination(staged_data, entry["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as source, destination.open("xb") as output:
                    while chunk := source.read(self.BUFFER_BYTES):
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        processed += len(chunk)
                        ratio = (
                            1.0
                            if manifest.source_bytes <= 0
                            else min(1.0, processed / manifest.source_bytes)
                        )
                        self._update(
                            operation_id,
                            stage="VERIFYING_FILES",
                            progress_percent=20 + round(ratio * 48),
                            message="ファイルを展開し、容量とSHA-256を検証しています。",
                            bytes_processed=processed,
                            total_bytes=manifest.source_bytes,
                            file_count=manifest.file_count,
                            source_bytes=manifest.source_bytes,
                            backup_created_at=manifest.created_at,
                            schema_revision=manifest.schema_revision,
                        )
                if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise self._invalid_archive("ファイルの容量またはSHA-256が一致しません。")
        return manifest

    def _parse_manifest(
        self,
        raw: object,
        info_by_name: dict[str, zipfile.ZipInfo],
    ) -> _ValidatedManifest:
        if not isinstance(raw, dict):
            raise self._invalid_archive("manifest.jsonの形式が正しくありません。")
        if raw.get("format") != "llm-ocr-portable-backup" or raw.get("formatVersion") != 1:
            raise self._invalid_archive("対応していないバックアップ形式です。")
        if raw.get("restoreTarget") != "backend/data":
            raise self._invalid_archive("復元対象が一致しません。")
        schema_revision = raw.get("databaseSchemaRevision")
        if not isinstance(schema_revision, str) or not schema_revision:
            raise self._invalid_archive("データベース版を確認できません。")
        try:
            created_at = datetime.fromisoformat(str(raw["createdAt"]))
            file_count = int(raw["fileCount"])
            source_bytes = int(raw["sourceBytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_archive("manifest.jsonの集計値が正しくありません。") from exc
        entries_raw = raw.get("files")
        if (
            not isinstance(entries_raw, list)
            or file_count != len(entries_raw)
            or file_count <= 0
            or source_bytes < 0
            or source_bytes > self.MAX_SOURCE_BYTES
        ):
            raise self._invalid_archive("manifest.jsonのファイル一覧が正しくありません。")
        entries: list[_ManifestEntry] = []
        paths: set[str] = set()
        calculated_bytes = 0
        for raw_entry in entries_raw:
            if not isinstance(raw_entry, dict):
                raise self._invalid_archive("manifest.jsonのファイル情報が正しくありません。")
            path = raw_entry.get("path")
            size = raw_entry.get("size")
            sha256 = raw_entry.get("sha256")
            if (
                not isinstance(path, str)
                or not isinstance(size, int)
                or size < 0
                or not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
            ):
                raise self._invalid_archive("manifest.jsonのファイル情報が正しくありません。")
            normalized_path = self._safe_manifest_path(path)
            lowered = normalized_path.lower()
            if lowered in paths:
                raise self._invalid_archive("manifest.jsonに重複パスがあります。")
            paths.add(lowered)
            archive_name = f"{self.ARCHIVE_ROOT}/{normalized_path}".lower()
            info = info_by_name.get(archive_name)
            if info is None or info.file_size != size:
                raise self._invalid_archive("manifest.jsonとZIPのファイル情報が一致しません。")
            if info.compress_size > 0 and info.file_size > max(
                info.compress_size * 100,
                128 * 1024 * 1024,
            ):
                raise self._invalid_archive("圧縮率が異常なZIPは取り込めません。")
            entries.append({"path": normalized_path, "size": size, "sha256": sha256})
            calculated_bytes += size
        if calculated_bytes != source_bytes or "data/app.db" not in paths:
            raise self._invalid_archive("manifest.jsonの合計またはDB情報が一致しません。")
        return _ValidatedManifest(
            created_at=created_at,
            schema_revision=schema_revision,
            file_count=file_count,
            source_bytes=source_bytes,
            entries=tuple(entries),
        )

    def _safe_manifest_path(self, supplied: str) -> str:
        if "\\" in supplied or supplied.startswith("/"):
            raise self._invalid_archive("ZIP内パスが正しくありません。")
        path = PurePosixPath(supplied)
        if (
            not path.parts
            or path.parts[0] != "data"
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(":" in part or part.endswith((" ", ".")) for part in path.parts)
            or any(
                part.split(".")[0].upper() in self._WINDOWS_RESERVED_NAMES
                for part in path.parts
            )
        ):
            raise self._invalid_archive("ZIP内パスが正しくありません。")
        if len(path.parts) > 2 and path.parts[1] not in self.INCLUDED_DIRECTORIES:
            raise self._invalid_archive("バックアップ対象外のパスが含まれています。")
        if len(path.parts) == 2 and path.as_posix() != "data/app.db":
            raise self._invalid_archive("バックアップ対象外のパスが含まれています。")
        return path.as_posix()

    def _staged_destination(self, staged_data: Path, manifest_path: str) -> Path:
        path = PurePosixPath(manifest_path)
        candidate = (staged_data / Path(*path.parts[1:])).resolve(strict=False)
        try:
            candidate.relative_to(staged_data.resolve())
        except ValueError as exc:
            raise self._invalid_archive("ZIP内パスが展開先を外れています。") from exc
        return candidate

    def _validate_staged_database(
        self,
        database_path: Path,
        manifest: _ValidatedManifest,
    ) -> None:
        self._update(
            self._operation_for_database_path(database_path),
            stage="VALIDATING_DATABASE",
            progress_percent=70,
            message="データベースの整合性とシステム版を確認しています。",
        )
        expected_revision = self._current_schema_revision()
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise self._invalid_archive("データベースの整合性検査に失敗しました。")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise self._invalid_archive("データベースの参照整合性検査に失敗しました。")
            revision_row = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            revision = str(revision_row[0]) if revision_row else ""
            if revision != manifest.schema_revision or revision != expected_revision:
                raise AppError(
                    status=409,
                    code="RESTORE_SCHEMA_MISMATCH",
                    title="Restore schema mismatch",
                    detail="バックアップと現在のシステム版が一致しません。",
                )
            active_statuses = ("CREATED", "QUEUED", "PROCESSING")
            placeholders = ",".join("?" for _ in active_statuses)
            active_count = connection.execute(
                f"SELECT COUNT(*) FROM processing_jobs WHERE status IN ({placeholders})",
                active_statuses,
            ).fetchone()[0]
            if active_count:
                raise self._invalid_archive("処理中ジョブを含むバックアップは取り込めません。")
            table_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "google_oauth_accounts" in table_names:
                connection.execute(
                    "UPDATE google_oauth_accounts "
                    "SET encrypted_refresh_token = '', is_active = 0"
                )
            connection.commit()
            connection.execute("PRAGMA journal_mode=DELETE")

    def _operation_for_database_path(self, database_path: Path) -> str:
        with self._state_lock:
            for operation_id, record in self._records.items():
                if record.staged_data / "app.db" == database_path:
                    return operation_id
        raise RuntimeError("Restore operation for staged database was not found")

    def _current_schema_revision(self) -> str:
        database_path = self._database_path()
        source_uri = f"file:{database_path.as_posix()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
        if row is None:
            raise AppError(
                status=500,
                code="RESTORE_SCHEMA_UNAVAILABLE",
                title="Restore schema unavailable",
                detail="現在のデータベース版を確認できません。",
            )
        return str(row[0])

    def _checkpoint_live_database(self) -> None:
        database_path = self._database_path()
        with closing(sqlite3.connect(database_path)) as connection:
            result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result is not None and int(result[0]) != 0:
            raise AppError(
                status=409,
                code="RESTORE_DATABASE_BUSY",
                title="Database is busy",
                detail="データベースが使用中です。画面処理の完了後に再実行してください。",
            )

    def _swap_in_staged_data(self, record: _ImportRecord) -> None:
        live_root = self._storage.root
        names = ("app.db", *self.INCLUDED_DIRECTORIES)
        record.swap_states = {}
        for name in names:
            live = live_root / name
            staged = record.staged_data / name
            rollback = record.rollback_dir / name
            state = _SwapState()
            record.swap_states[name] = state
            if live.exists():
                os.replace(live, rollback)
                state.live_moved = True
            if staged.exists():
                os.replace(staged, live)
                state.staged_installed = True
            elif name != "app.db":
                live.mkdir(parents=True, exist_ok=True)
                state.staged_installed = True
            else:
                raise self._not_ready_error()
        for suffix in ("-wal", "-shm"):
            (live_root / f"app.db{suffix}").unlink(missing_ok=True)

    def _rollback_filesystem(self, record: _ImportRecord) -> None:
        live_root = self._storage.root
        record.staged_data.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            (live_root / f"app.db{suffix}").unlink(missing_ok=True)
        swap_states = record.swap_states or {}
        for name in reversed(tuple(swap_states)):
            state = swap_states[name]
            live = live_root / name
            staged = record.staged_data / name
            rollback = record.rollback_dir / name
            if state.staged_installed and live.exists():
                if staged.exists():
                    if staged.is_dir():
                        shutil.rmtree(staged)
                    else:
                        staged.unlink()
                os.replace(live, staged)
            if state.live_moved and rollback.exists():
                os.replace(rollback, live)
        record.swap_states = None
        self._storage.initialize()
        self._database.check_connection()

    def _database_path(self) -> Path:
        database_name = self._database.engine.url.database
        if not database_name or database_name == ":memory:":
            raise AppError(
                status=500,
                code="RESTORE_DATABASE_UNAVAILABLE",
                title="Restore database unavailable",
                detail="ファイル形式のデータベースを確認できません。",
            )
        path = Path(database_name).expanduser().resolve()
        if not path.is_file():
            raise AppError(
                status=500,
                code="RESTORE_DATABASE_UNAVAILABLE",
                title="Restore database unavailable",
                detail="現在のデータベースが見つかりません。",
            )
        return path

    def _record(self, operation_id: str) -> _ImportRecord:
        with self._state_lock:
            record = self._records.get(operation_id)
        if record is None:
            raise self._not_ready_error()
        return record

    def _update(
        self,
        operation_id: str,
        *,
        status: Literal["RUNNING", "READY", "APPLYING", "COMPLETED", "FAILED"] | None = None,
        stage: str | None = None,
        progress_percent: int | None = None,
        message: str | None = None,
        file_bytes: int | None = None,
        bytes_processed: int | None = None,
        total_bytes: int | None = None,
        file_count: int | None = None,
        source_bytes: int | None = None,
        backup_created_at: datetime | None = None,
        schema_revision: str | None = None,
        rollback_expires_at: datetime | None = None,
    ) -> None:
        with self._state_lock:
            progress = self._records[operation_id].progress
            if status is not None:
                progress.status = status
            if stage is not None:
                progress.stage = stage
            if progress_percent is not None:
                progress.progress_percent = max(0, min(100, progress_percent))
            if message is not None:
                progress.message = message
            if file_bytes is not None:
                progress.file_bytes = file_bytes
            if bytes_processed is not None:
                progress.bytes_processed = bytes_processed
            if total_bytes is not None:
                progress.total_bytes = total_bytes
            if file_count is not None:
                progress.file_count = file_count
            if source_bytes is not None:
                progress.source_bytes = source_bytes
            if backup_created_at is not None:
                progress.backup_created_at = backup_created_at
            if schema_revision is not None:
                progress.schema_revision = schema_revision
            if rollback_expires_at is not None:
                progress.rollback_expires_at = rollback_expires_at
            progress.error_code = None
            progress.error_message = None
            progress.updated_at = datetime.now(UTC)

    def _fail(self, operation_id: str, error: AppError) -> None:
        with self._state_lock:
            record = self._records.get(operation_id)
            if record is None:
                return
            progress = record.progress
            progress.status = "FAILED"
            progress.stage = "FAILED"
            progress.message = error.detail
            progress.error_code = error.code
            progress.error_message = error.detail
            progress.updated_at = datetime.now(UTC)

    def _operation_id(self, operation_id: str) -> str:
        try:
            parsed = UUID(operation_id)
        except ValueError as exc:
            raise AppError(
                status=422,
                code="RESTORE_OPERATION_ID_INVALID",
                title="Invalid restore operation id",
                detail="取り込み処理IDが正しくありません。",
            ) from exc
        normalized = str(parsed)
        if normalized != operation_id:
            raise AppError(
                status=422,
                code="RESTORE_OPERATION_ID_INVALID",
                title="Invalid restore operation id",
                detail="取り込み処理IDが正しくありません。",
            )
        return normalized

    def _imports_root(self) -> Path:
        root = self._storage.resolve("tmp/portable-imports")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _rollbacks_root(self) -> Path:
        root = self._storage.resolve("tmp/portable-restore-rollbacks")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _cleanup_stale_paths(self, root: Path) -> None:
        threshold = datetime.now(UTC).timestamp() - self.STALE_FILE_AGE.total_seconds()
        for path in root.iterdir():
            try:
                if path.stat().st_mtime < threshold:
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
            except OSError:
                continue

    @staticmethod
    def _zip_info_is_link(info: zipfile.ZipInfo) -> bool:
        return stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK

    @staticmethod
    def _invalid_archive(detail: str) -> AppError:
        return AppError(
            status=422,
            code="RESTORE_ARCHIVE_INVALID",
            title="Invalid portable backup",
            detail=detail,
        )

    @staticmethod
    def _not_ready_error() -> AppError:
        return AppError(
            status=409,
            code="RESTORE_NOT_READY",
            title="Restore is not ready",
            detail="検証済みの取り込みデータが見つかりません。",
        )
