from __future__ import annotations

import errno
import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.errors import AppError
from app.db.models import ProcessingJob
from app.db.session import Database
from app.domain.enums import JobStatus
from app.storage.local_storage import LocalStorageService


@dataclass(frozen=True, slots=True)
class PortableBackup:
    path: Path
    filename: str
    backup_id: str
    file_count: int
    source_bytes: int
    file_bytes: int
    created_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class PortableBackupProgress:
    operation_id: str
    status: Literal["RUNNING", "READY", "FAILED"]
    stage: str
    progress_percent: int
    message: str
    bytes_processed: int
    total_bytes: int | None
    backup_id: str | None
    error_code: str | None
    error_message: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class _SourceFile:
    path: Path
    archive_path: str
    size: int
    modified_ns: int


class _ManifestEntry(TypedDict):
    path: str
    size: int
    sha256: str


class PortableBackupService:
    """Create a complete saved-data snapshot for copying between local PCs."""

    INCLUDED_DIRECTORIES = ("uploads", "artifacts", "templates", "exports")
    ACTIVE_JOB_STATUSES = (
        JobStatus.CREATED.value,
        JobStatus.QUEUED.value,
        JobStatus.PROCESSING.value,
    )
    ARCHIVE_ROOT = "llm-ocr-portable-backup"
    BUFFER_BYTES = 1024 * 1024
    MINIMUM_FREE_MARGIN_BYTES = 64 * 1024 * 1024
    STALE_FILE_AGE = timedelta(hours=24)

    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._lock = threading.Lock()
        self._mutation_state_lock = threading.Lock()
        self._operation_kind: Literal["backup", "restore"] | None = None
        self._active_requests = 0
        self._active_mutations = 0
        self._progress_lock = threading.Lock()
        self._progress: dict[str, PortableBackupProgress] = {}
        self._remove_stale_files(self._backup_dir())

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def begin_request(self, *, mutating: bool) -> bool:
        """Register an HTTP request unless an exclusive operation blocks it."""
        with self._mutation_state_lock:
            if self._operation_kind == "restore":
                return False
            if mutating and self._operation_kind == "backup":
                return False
            self._active_requests += 1
            if mutating:
                self._active_mutations += 1
            return True

    def end_request(self, *, mutating: bool) -> None:
        with self._mutation_state_lock:
            if self._active_requests <= 0:
                raise RuntimeError("Portable backup request counter is unbalanced")
            self._active_requests -= 1
            if mutating:
                if self._active_mutations <= 0:
                    raise RuntimeError("Portable backup mutation counter is unbalanced")
                self._active_mutations -= 1

    def begin_mutation(self) -> bool:
        return self.begin_request(mutating=True)

    def end_mutation(self) -> None:
        self.end_request(mutating=True)

    def begin_restore(self) -> None:
        self._acquire_operation("restore")
        try:
            self._ensure_no_active_requests()
            self._ensure_no_active_jobs()
        except Exception:
            self.end_exclusive_operation()
            raise

    def end_exclusive_operation(self) -> None:
        with self._mutation_state_lock:
            self._operation_kind = None
        self._lock.release()

    def create(self, operation_id: str | None = None) -> PortableBackup:
        normalized_operation_id = self._operation_id(operation_id)
        self._start_progress(normalized_operation_id)
        try:
            self._acquire_operation("backup")
        except AppError as exc:
            self._fail_progress(normalized_operation_id, exc)
            raise

        archive_path: Path | None = None
        work_dir: Path | None = None
        completed = False
        try:
            self._ensure_no_active_mutations()
            self._ensure_no_active_jobs()
            self._update_progress(
                normalized_operation_id,
                stage="PREPARING",
                progress_percent=5,
                message="保存データと空き容量を確認しています。",
            )
            backup_dir = self._backup_dir()
            self._remove_stale_files(backup_dir)

            backup_id = str(uuid4())
            timestamp = datetime.now(UTC)
            expires_at = timestamp + self.STALE_FILE_AGE
            filename = timestamp.astimezone().strftime(
                "llm-ocr-portable-backup-%Y%m%d-%H%M%S.zip"
            )
            work_dir = Path(
                tempfile.mkdtemp(prefix=f"{backup_id}-", dir=backup_dir)
            )
            archive_path = backup_dir / f"{backup_id}.zip"
            database_path = self._database_path()
            snapshot_path = work_dir / "app.db"
            source_uri = f"file:{database_path.as_posix()}?mode=ro"
            with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
                source_version = int(
                    source_connection.execute("PRAGMA data_version").fetchone()[0]
                )
                source_files = self._source_files()
                self._ensure_free_space(database_path, source_files, backup_dir)
                self._update_progress(
                    normalized_operation_id,
                    stage="SNAPSHOTTING_DATABASE",
                    progress_percent=10,
                    message="データベースの整合スナップショットを作成しています。",
                )
                self._copy_database(
                    source_connection,
                    snapshot_path,
                    normalized_operation_id,
                )
                schema_revision = self._validate_database(snapshot_path)
                total_source_bytes = snapshot_path.stat().st_size + sum(
                    item.size for item in source_files
                )
                self._update_progress(
                    normalized_operation_id,
                    stage="WRITING_ARCHIVE",
                    progress_percent=35,
                    message="帳票画像と保存データをZIPへ書き込んでいます。",
                    total_bytes=total_source_bytes,
                )
                manifest_entries = self._write_archive(
                    archive_path=archive_path,
                    snapshot_path=snapshot_path,
                    source_files=source_files,
                    backup_id=backup_id,
                    created_at=timestamp,
                    schema_revision=schema_revision,
                    operation_id=normalized_operation_id,
                    total_source_bytes=total_source_bytes,
                )
                self._update_progress(
                    normalized_operation_id,
                    stage="VERIFYING_ARCHIVE",
                    progress_percent=92,
                    message="作成したバックアップの整合性を確認しています。",
                )
                self._ensure_source_unchanged(source_connection, source_version)
            self._ensure_no_active_jobs()
            backup = PortableBackup(
                path=archive_path,
                filename=filename,
                backup_id=backup_id,
                file_count=len(manifest_entries),
                source_bytes=sum(item["size"] for item in manifest_entries),
                file_bytes=archive_path.stat().st_size,
                created_at=timestamp,
                expires_at=expires_at,
            )
            self._write_download_metadata(backup)
            self._update_progress(
                normalized_operation_id,
                status="READY",
                stage="READY",
                progress_percent=100,
                message="バックアップの作成が完了しました。",
                bytes_processed=backup.source_bytes,
                total_bytes=backup.source_bytes,
                backup_id=backup.backup_id,
            )
            completed = True
            return backup
        except AppError as exc:
            self._fail_progress(normalized_operation_id, exc)
            raise
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                error = AppError(
                    status=507,
                    code="BACKUP_STORAGE_INSUFFICIENT",
                    title="Insufficient backup storage",
                    detail="バックアップ作成用の空き容量が不足しています。",
                )
                self._fail_progress(normalized_operation_id, error)
                raise error from exc
            error = AppError(
                status=500,
                code="BACKUP_CREATE_FAILED",
                title="Backup creation failed",
                detail="持ち出しバックアップを作成できませんでした。保存データを確認してください。",
            )
            self._fail_progress(normalized_operation_id, error)
            raise error from exc
        finally:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)
            if archive_path is not None and not completed:
                self._remove_generated_files(archive_path)
            self.end_exclusive_operation()

    def operation(self, operation_id: str) -> PortableBackupProgress:
        normalized_id = self._operation_id(operation_id)
        with self._progress_lock:
            progress = self._progress.get(normalized_id)
            if progress is None:
                raise AppError(
                    status=404,
                    code="BACKUP_OPERATION_NOT_FOUND",
                    title="Backup operation not found",
                    detail="バックアップの進捗情報が見つかりません。",
                )
            return replace(progress)

    def _acquire_operation(self, kind: Literal["backup", "restore"]) -> None:
        if not self._lock.acquire(blocking=False):
            raise AppError(
                status=409,
                code="BACKUP_OPERATION_RUNNING",
                title="Backup operation already running",
                detail="バックアップの書き出しまたは取り込みを処理中です。",
            )
        with self._mutation_state_lock:
            self._operation_kind = kind

    def _ensure_no_active_requests(self) -> None:
        with self._mutation_state_lock:
            has_active_requests = self._active_requests > 0
        if has_active_requests:
            raise AppError(
                status=409,
                code="RESTORE_REQUESTS_ACTIVE",
                title="Requests are still active",
                detail="別の画面処理が進行中です。完了後に取り込みを再実行してください。",
            )

    def _ensure_no_active_mutations(self) -> None:
        with self._mutation_state_lock:
            has_active_mutations = self._active_mutations > 0
        if has_active_mutations:
            raise AppError(
                status=409,
                code="BACKUP_MUTATIONS_ACTIVE",
                title="Saved data is still changing",
                detail=(
                    "保存または編集処理が進行中です。完了後にバックアップを"
                    "再実行してください。"
                ),
            )

    def _operation_id(self, operation_id: str | None) -> str:
        supplied = operation_id or str(uuid4())
        try:
            parsed = UUID(supplied)
        except ValueError as exc:
            raise AppError(
                status=422,
                code="BACKUP_OPERATION_ID_INVALID",
                title="Invalid backup operation id",
                detail="バックアップ処理IDが正しくありません。",
            ) from exc
        normalized = str(parsed)
        if normalized != supplied:
            raise AppError(
                status=422,
                code="BACKUP_OPERATION_ID_INVALID",
                title="Invalid backup operation id",
                detail="バックアップ処理IDが正しくありません。",
            )
        return normalized

    def _start_progress(self, operation_id: str) -> None:
        with self._progress_lock:
            self._progress[operation_id] = PortableBackupProgress(
                operation_id=operation_id,
                status="RUNNING",
                stage="QUEUED",
                progress_percent=1,
                message="バックアップ作成を開始します。",
                bytes_processed=0,
                total_bytes=None,
                backup_id=None,
                error_code=None,
                error_message=None,
                updated_at=datetime.now(UTC),
            )
            if len(self._progress) > 50:
                oldest = sorted(
                    self._progress.values(), key=lambda item: item.updated_at
                )[:-50]
                for item in oldest:
                    self._progress.pop(item.operation_id, None)

    def _update_progress(
        self,
        operation_id: str,
        *,
        status: Literal["RUNNING", "READY", "FAILED"] | None = None,
        stage: str | None = None,
        progress_percent: int | None = None,
        message: str | None = None,
        bytes_processed: int | None = None,
        total_bytes: int | None = None,
        backup_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._progress_lock:
            current = self._progress[operation_id]
            if status is not None:
                current.status = status
            if stage is not None:
                current.stage = stage
            if progress_percent is not None:
                current.progress_percent = max(0, min(100, progress_percent))
            if message is not None:
                current.message = message
            if bytes_processed is not None:
                current.bytes_processed = bytes_processed
            if total_bytes is not None:
                current.total_bytes = total_bytes
            if backup_id is not None:
                current.backup_id = backup_id
            current.error_code = error_code
            current.error_message = error_message
            current.updated_at = datetime.now(UTC)

    def _fail_progress(self, operation_id: str, error: AppError) -> None:
        self._update_progress(
            operation_id,
            status="FAILED",
            stage="FAILED",
            message=error.detail,
            error_code=error.code,
            error_message=error.detail,
        )

    def get(self, backup_id: str) -> PortableBackup:
        normalized_id = self._validated_backup_id(backup_id)
        backup_dir = self._backup_dir()
        self._remove_stale_files(backup_dir)
        metadata_path = backup_dir / f"{normalized_id}.json"
        archive_path = backup_dir / f"{normalized_id}.zip"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            backup = PortableBackup(
                path=archive_path,
                filename=str(metadata["filename"]),
                backup_id=normalized_id,
                file_count=int(metadata["fileCount"]),
                source_bytes=int(metadata["sourceBytes"]),
                file_bytes=int(metadata["fileBytes"]),
                created_at=datetime.fromisoformat(str(metadata["createdAt"])),
                expires_at=datetime.fromisoformat(str(metadata["expiresAt"])),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise self._not_found_error() from exc
        if not archive_path.is_file() or archive_path.stat().st_size != backup.file_bytes:
            self.delete(normalized_id)
            raise self._not_found_error()
        if backup.expires_at <= datetime.now(UTC):
            self.delete(normalized_id)
            raise AppError(
                status=410,
                code="BACKUP_EXPIRED",
                title="Backup expired",
                detail="一時バックアップの保存期限が切れました。もう一度作成してください。",
            )
        return backup

    def delete(self, backup_id: str) -> None:
        normalized_id = self._validated_backup_id(backup_id)
        backup_dir = self._backup_dir()
        (backup_dir / f"{normalized_id}.zip").unlink(missing_ok=True)
        (backup_dir / f"{normalized_id}.json").unlink(missing_ok=True)

    def _backup_dir(self) -> Path:
        backup_dir = self._storage.resolve("tmp/portable-backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def _validated_backup_id(self, backup_id: str) -> str:
        try:
            parsed = UUID(backup_id)
        except ValueError as exc:
            raise self._not_found_error() from exc
        normalized = str(parsed)
        if normalized != backup_id:
            raise self._not_found_error()
        return normalized

    def _not_found_error(self) -> AppError:
        return AppError(
            status=404,
            code="BACKUP_NOT_FOUND",
            title="Backup not found",
            detail="一時バックアップが見つかりません。もう一度作成してください。",
        )

    def _write_download_metadata(self, backup: PortableBackup) -> None:
        metadata_path = backup.path.with_suffix(".json")
        temporary_path = metadata_path.with_suffix(".json.part")
        metadata = {
            "filename": backup.filename,
            "fileCount": backup.file_count,
            "sourceBytes": backup.source_bytes,
            "fileBytes": backup.file_bytes,
            "createdAt": backup.created_at.isoformat(),
            "expiresAt": backup.expires_at.isoformat(),
        }
        temporary_path.write_text(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(metadata_path)

    @staticmethod
    def _remove_generated_files(archive_path: Path) -> None:
        archive_path.unlink(missing_ok=True)
        archive_path.with_suffix(".json").unlink(missing_ok=True)
        archive_path.with_suffix(".json.part").unlink(missing_ok=True)

    def _ensure_no_active_jobs(self) -> None:
        with self._database.session() as session:
            active_count = session.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(ProcessingJob.status.in_(self.ACTIVE_JOB_STATUSES))
            )
        if active_count:
            raise AppError(
                status=409,
                code="BACKUP_ACTIVE_JOBS",
                title="Jobs are still active",
                detail=(
                    "処理中または待機中の読み取りがあります。完了またはキャンセル後に"
                    "バックアップしてください。"
                ),
            )

    def _database_path(self) -> Path:
        database_name = self._database.engine.url.database
        if not database_name or database_name == ":memory:":
            raise AppError(
                status=500,
                code="BACKUP_DATABASE_UNAVAILABLE",
                title="Backup database unavailable",
                detail="ファイル形式のデータベースを確認できませんでした。",
            )
        path = Path(database_name).expanduser().resolve()
        if not path.is_file():
            raise AppError(
                status=500,
                code="BACKUP_DATABASE_UNAVAILABLE",
                title="Backup database unavailable",
                detail="バックアップ対象のデータベースが見つかりません。",
            )
        return path

    def _source_files(self) -> list[_SourceFile]:
        result: list[_SourceFile] = []
        for directory_name in self.INCLUDED_DIRECTORIES:
            root = self._storage.resolve(directory_name)
            if not root.exists():
                continue
            if root.is_symlink() or root.is_junction():
                raise self._unsafe_path_error()
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or path.is_junction():
                    raise self._unsafe_path_error()
                if not path.is_file() or path.name == ".gitkeep":
                    continue
                stat = path.stat()
                relative = path.relative_to(self._storage.root).as_posix()
                result.append(
                    _SourceFile(
                        path=path,
                        archive_path=f"{self.ARCHIVE_ROOT}/data/{relative}",
                        size=stat.st_size,
                        modified_ns=stat.st_mtime_ns,
                    )
                )
        return result

    def _unsafe_path_error(self) -> AppError:
        return AppError(
            status=409,
            code="BACKUP_UNSAFE_PATH",
            title="Unsafe backup path",
            detail="保存データ内に外部を参照するリンクがあるため中止しました。",
        )

    def _ensure_free_space(
        self,
        database_path: Path,
        source_files: list[_SourceFile],
        backup_dir: Path,
    ) -> None:
        database_bytes = database_path.stat().st_size
        wal_path = database_path.with_name(f"{database_path.name}-wal")
        if wal_path.is_file():
            database_bytes += wal_path.stat().st_size
        source_bytes = sum(item.size for item in source_files)
        required = source_bytes + (database_bytes * 2) + self.MINIMUM_FREE_MARGIN_BYTES
        if shutil.disk_usage(backup_dir).free < required:
            raise AppError(
                status=507,
                code="BACKUP_STORAGE_INSUFFICIENT",
                title="Insufficient backup storage",
                detail=(
                    "バックアップ作成用の空き容量が不足しています。"
                    "システムドライブの空き容量を増やして再実行してください。"
                ),
            )

    def _copy_database(
        self,
        source: sqlite3.Connection,
        destination_path: Path,
        operation_id: str,
    ) -> None:
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(
                destination,
                pages=256,
                progress=lambda _status, remaining, total: self._update_progress(
                    operation_id,
                    stage="SNAPSHOTTING_DATABASE",
                    progress_percent=(
                        10 if total <= 0 else 10 + round(((total - remaining) / total) * 20)
                    ),
                    message="データベースの整合スナップショットを作成しています。",
                ),
            )

    def _validate_database(self, snapshot_path: Path) -> str | None:
        with closing(sqlite3.connect(snapshot_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
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
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise AppError(
                    status=500,
                    code="BACKUP_DATABASE_INVALID",
                    title="Backup database invalid",
                    detail="データベースの整合性検査に失敗したため中止しました。",
                )
            foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_issues:
                raise AppError(
                    status=500,
                    code="BACKUP_DATABASE_INVALID",
                    title="Backup database invalid",
                    detail="データベースの参照整合性検査に失敗したため中止しました。",
                )
            try:
                row = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
        return str(row[0]) if row else None

    def _write_archive(
        self,
        *,
        archive_path: Path,
        snapshot_path: Path,
        source_files: list[_SourceFile],
        backup_id: str,
        created_at: datetime,
        schema_revision: str | None,
        operation_id: str,
        total_source_bytes: int,
    ) -> list[_ManifestEntry]:
        entries: list[_ManifestEntry] = []
        processed_bytes = 0

        def report_progress(chunk_bytes: int) -> None:
            nonlocal processed_bytes
            processed_bytes += chunk_bytes
            ratio = (
                1.0
                if total_source_bytes <= 0
                else min(1.0, processed_bytes / total_source_bytes)
            )
            self._update_progress(
                operation_id,
                stage="WRITING_ARCHIVE",
                progress_percent=35 + round(ratio * 52),
                message="帳票画像と保存データをZIPへ書き込んでいます。",
                bytes_processed=processed_bytes,
                total_bytes=total_source_bytes,
            )

        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            entries.append(
                self._add_file(
                    archive,
                    snapshot_path,
                    f"{self.ARCHIVE_ROOT}/data/app.db",
                    report_progress,
                )
            )
            for item in source_files:
                entries.append(self._add_source_file(archive, item, report_progress))

            manifest = {
                "format": "llm-ocr-portable-backup",
                "formatVersion": 1,
                "backupId": backup_id,
                "createdAt": created_at.isoformat(),
                "databaseSchemaRevision": schema_revision,
                "restoreTarget": "backend/data",
                "requiresLocalCopyBeforeUse": True,
                "includedDirectories": list(self.INCLUDED_DIRECTORIES),
                "excluded": [
                    "logs",
                    "tmp",
                    "app.db-wal",
                    "app.db-shm",
                    "API keys and local environment settings",
                    "Google OAuth refresh tokens",
                ],
                "sanitizedDatabaseTables": [
                    "google_oauth_accounts.encrypted_refresh_token",
                    "google_oauth_accounts.is_active",
                ],
                "fileCount": len(entries),
                "sourceBytes": sum(item["size"] for item in entries),
                "files": entries,
            }
            archive.writestr(
                f"{self.ARCHIVE_ROOT}/manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            archive.writestr(
                f"{self.ARCHIVE_ROOT}/復元手順.txt",
                self._restore_guide().encode("utf-8-sig"),
            )
            archive.writestr(
                f"{self.ARCHIVE_ROOT}/バックアップ検証.ps1",
                self._verification_script().encode("utf-8-sig"),
            )
        return entries

    def _add_source_file(
        self,
        archive: zipfile.ZipFile,
        source: _SourceFile,
        progress: Callable[[int], None] | None = None,
    ) -> _ManifestEntry:
        try:
            before = source.path.stat()
        except FileNotFoundError as exc:
            raise self._data_changed_error() from exc
        if before.st_size != source.size or before.st_mtime_ns != source.modified_ns:
            raise self._data_changed_error()
        entry = self._add_file(
            archive,
            source.path,
            source.archive_path,
            progress,
        )
        try:
            after = source.path.stat()
        except FileNotFoundError as exc:
            raise self._data_changed_error() from exc
        if after.st_size != source.size or after.st_mtime_ns != source.modified_ns:
            raise self._data_changed_error()
        return entry

    def _add_file(
        self,
        archive: zipfile.ZipFile,
        source_path: Path,
        archive_path: str,
        progress: Callable[[int], None] | None = None,
    ) -> _ManifestEntry:
        digest = hashlib.sha256()
        size = 0
        info = zipfile.ZipInfo(archive_path)
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o600 << 16
        with (
            source_path.open("rb") as source,
            archive.open(info, mode="w", force_zip64=True) as destination,
        ):
            while chunk := source.read(self.BUFFER_BYTES):
                destination.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                if progress is not None:
                    progress(len(chunk))
        return {
            "path": archive_path.removeprefix(f"{self.ARCHIVE_ROOT}/"),
            "size": size,
            "sha256": digest.hexdigest(),
        }

    def _ensure_source_unchanged(
        self,
        source: sqlite3.Connection,
        expected_version: int,
    ) -> None:
        current_version = int(source.execute("PRAGMA data_version").fetchone()[0])
        if current_version != expected_version:
            raise self._data_changed_error()

    def _data_changed_error(self) -> AppError:
        return AppError(
            status=409,
            code="BACKUP_DATA_CHANGED",
            title="Saved data changed during backup",
            detail=(
                "作成中に保存データが変更されたため中止しました。"
                "読み取りや編集を終えてから再実行してください。"
            ),
        )

    def _remove_stale_files(self, backup_dir: Path) -> None:
        threshold = datetime.now(UTC).timestamp() - self.STALE_FILE_AGE.total_seconds()
        for path in backup_dir.iterdir():
            if path.is_file() and path.suffix in {".zip", ".json", ".part"}:
                try:
                    if path.stat().st_mtime < threshold:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
            elif path.is_dir():
                try:
                    if path.stat().st_mtime < threshold:
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    continue

    @staticmethod
    def _restore_guide() -> str:
        return """帳票 OCR V2 持ち出しバックアップ 復元手順

このZIPはバックアップ保管用です。外付けSSD上からシステムを直接起動しないでください。
帳票画像や確定済みデータを含む非暗号化ZIPです。BitLocker等で暗号化した外付け媒体へ
保存し、紛失時に第三者が読み出せない状態で管理してください。

1. 別PCに同じ版の帳票 OCR V2をセットアップします。
2. このZIPを外付けSSDから別PCのローカルディスクへコピーします。
3. 帳票 OCR V2を停止します。
4. ZIPをローカルディスク上の新しい空フォルダーへ展開し、同梱の
   「バックアップ検証.ps1」を実行します。既存フォルダーへ上書き展開しないでください。
5. 検証完了後、ZIP内の data フォルダーを別PCの backend/data として復元します。
   既存の backend/data は先に別名で退避し、ファイルを混在させないでください。
6. 帳票 OCR V2を起動し、データベース接続と保存済み書類を確認します。

APIキー、Google OAuthクライアント秘密情報、AUTH_JWT_SECRET、その他のローカル設定は
このバックアップに含まれません。別PCで個別に設定してください。持ち出し用DB内の
Google更新トークンは無効化されるため、復元後はGoogle連携の再認証が必須です。
"""

    @staticmethod
    def _verification_script() -> str:
        return r"""$ErrorActionPreference = 'Stop'
$backupRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$manifestPath = Join-Path -Path $backupRoot -ChildPath 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'manifest.json が見つかりません。'
}
$manifest = Get-Content -Raw -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
$verifiedCount = 0
$verifiedBytes = [int64]0
$separator = [System.IO.Path]::DirectorySeparatorChar
$rootPrefix = $backupRoot.TrimEnd($separator) + $separator
$dataRoot = Join-Path -Path $backupRoot -ChildPath 'data'
if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
    throw 'data フォルダーが見つかりません。'
}
$reparsePoints = @(
    Get-ChildItem -LiteralPath $dataRoot -Force -Recurse |
        Where-Object {
            ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        }
)
if ($reparsePoints.Count -gt 0) {
    throw "data フォルダー内に外部参照リンクがあります: $($reparsePoints[0].FullName)"
}
$expectedPaths = [System.Collections.Generic.List[string]]::new()
$expectedPathSet = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($entry in $manifest.files) {
    $relativePath = ([string]$entry.path).Replace('\', '/')
    $pathIsRooted = [System.IO.Path]::IsPathRooted($relativePath)
    $containsParent = $relativePath.Split('/') -contains '..'
    $isDataFile = $relativePath.StartsWith(
        'data/',
        [System.StringComparison]::OrdinalIgnoreCase
    )
    if ($pathIsRooted -or $containsParent -or -not $isDataFile) {
        throw "不正な相対パスです: $relativePath"
    }
    if (-not $expectedPathSet.Add($relativePath)) {
        throw "manifest.json に重複パスがあります: $relativePath"
    }
    $expectedPaths.Add($relativePath)
    $joinedPath = Join-Path -Path $backupRoot -ChildPath $relativePath
    $candidate = [System.IO.Path]::GetFullPath($joinedPath)
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "バックアップ外を参照するパスです: $relativePath"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "ファイルが見つかりません: $relativePath"
    }
    $file = Get-Item -LiteralPath $candidate
    if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "外部参照リンクは検証できません: $relativePath"
    }
    if ([int64]$file.Length -ne [int64]$entry.size) {
        throw "ファイル容量が一致しません: $relativePath"
    }
    $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne ([string]$entry.sha256).ToLowerInvariant()) {
        throw "SHA-256が一致しません: $relativePath"
    }
    $verifiedCount += 1
    $verifiedBytes += [int64]$file.Length
}
if ($verifiedCount -ne [int]$manifest.fileCount) {
    throw 'manifest.json のファイル数と検証数が一致しません。'
}
if ($verifiedBytes -ne [int64]$manifest.sourceBytes) {
    throw 'manifest.json の合計容量と検証容量が一致しません。'
}
$actualPaths = [System.Collections.Generic.List[string]]::new()
$actualPathSet = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($file in Get-ChildItem -LiteralPath $dataRoot -Force -File -Recurse) {
    $candidate = [System.IO.Path]::GetFullPath($file.FullName)
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "data フォルダー外を参照するファイルがあります: $candidate"
    }
    $relativePath = $candidate.Substring($rootPrefix.Length).Replace('\', '/')
    if (-not $actualPathSet.Add($relativePath)) {
        throw "展開先に重複パスがあります: $relativePath"
    }
    $actualPaths.Add($relativePath)
}
$missingPaths = @($expectedPaths | Where-Object { -not $actualPathSet.Contains($_) })
$unexpectedPaths = @($actualPaths | Where-Object { -not $expectedPathSet.Contains($_) })
if ($missingPaths.Count -gt 0) {
    throw "data フォルダーに不足ファイルがあります: $($missingPaths[0])"
}
if ($unexpectedPaths.Count -gt 0) {
    throw "data フォルダーに未記載ファイルがあります: $($unexpectedPaths[0])"
}
Write-Host "検証完了: $verifiedCount ファイル / $verifiedBytes bytes" -ForegroundColor Green
"""
