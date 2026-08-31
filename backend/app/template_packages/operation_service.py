from __future__ import annotations

import hashlib
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import uuid4

from app.core.errors import AppError
from app.db.session import Database
from app.storage.local_storage import LocalStorageService
from app.template_packages.export_models import (
    TemplateExportInspection,
    TemplateExportRequest,
    TemplatePackageExport,
    TemplatePackageExportError,
)
from app.template_packages.export_service import TemplateExportService
from app.template_packages.import_models import (
    TemplateImportInspection,
    TemplatePackageImportError,
    TemplatePackageImportResult,
)
from app.template_packages.import_service import (
    TemplateImportInspectionService,
    TemplateImportService,
)
from app.template_packages.package_reader import TemplatePackageReader
from app.template_packages.security_limits import DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS

OperationKind = Literal["IMPORT", "EXPORT"]
OperationStatus = Literal["READY", "RUNNING", "COMPLETED", "FAILED"]
ErrorCategory = Literal["SECURITY", "COMPATIBILITY", "CONFLICT", "VALIDATION", "SYSTEM"]


@dataclass(slots=True)
class TemplatePackageOperation:
    operation_id: str
    kind: OperationKind
    status: OperationStatus
    stage: str
    progress_percent: int
    message: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    file_name: str | None = None
    package_sha256: str | None = None
    confirmation_token: str | None = None
    inspection: TemplateImportInspection | TemplateExportInspection | None = None
    result: TemplatePackageImportResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_category: ErrorCategory | None = None


@dataclass(slots=True)
class _TokenRecord:
    token_hash: str
    purpose: OperationKind
    operation_id: str
    expires_at: datetime
    package_sha256: str | None = None
    export_request: TemplateExportRequest | None = None
    confirmation_digest: str | None = None
    import_source_type: Literal["FILE", "DRIVE"] = "FILE"
    consumed: bool = False


class TemplatePackageOperationService:
    """Owns short-lived inspection state without creating a second package reader."""

    TOKEN_TTL = timedelta(minutes=10)
    OPERATION_TTL = timedelta(minutes=30)
    READ_CHUNK_BYTES = 1024 * 1024

    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._inspection = TemplateImportInspectionService(database)
        self._importer = TemplateImportService(database, storage)
        self._exporter = TemplateExportService(database, storage)
        self._reader = TemplatePackageReader()
        self._lock = threading.Lock()
        self._operations: dict[str, TemplatePackageOperation] = {}
        self._tokens: dict[str, _TokenRecord] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="template-package")
        self._closed = False
        self._cleanup_stale_files()

    def inspect_import(
        self,
        filename: str,
        source: BinaryIO,
        *,
        source_type: Literal["FILE", "DRIVE"] = "FILE",
    ) -> TemplatePackageOperation:
        self._cleanup_expired()
        safe_name = Path(filename or "template.llmocr-template").name
        if not safe_name.lower().endswith(".llmocr-template"):
            raise AppError(
                status=415,
                code="TEMPLATE_PACKAGE_FILE_TYPE_INVALID",
                title="Invalid template package file type",
                detail=".llmocr-templateファイルを選択してください。",
            )
        content = self._read_bounded(source)
        try:
            inspection = self._inspection.inspect(content)
        except TemplatePackageImportError as exc:
            raise self._import_app_error(exc) from exc
        operation_id = str(uuid4())
        relative_path = self._import_path(operation_id)
        self._storage.write_bytes(relative_path, content)
        now = datetime.now(UTC)
        token, token_hash = self._new_token()
        operation = TemplatePackageOperation(
            operation_id=operation_id,
            kind="IMPORT",
            status="READY",
            stage="READY_TO_IMPORT",
            progress_percent=55,
            message="packageの検証が完了しました。内容を確認してください。",
            created_at=now,
            updated_at=now,
            expires_at=now + self.OPERATION_TTL,
            file_name=safe_name,
            package_sha256=inspection.package_sha256,
            confirmation_token=None,
            inspection=inspection,
        )
        with self._lock:
            self._operations[operation_id] = operation
            self._tokens[token_hash] = _TokenRecord(
                token_hash=token_hash,
                purpose="IMPORT",
                operation_id=operation_id,
                expires_at=now + self.TOKEN_TTL,
                package_sha256=inspection.package_sha256,
                import_source_type=source_type,
            )
        return replace(operation, confirmation_token=token)

    def inspect_export(self, request: TemplateExportRequest) -> TemplatePackageOperation:
        self._cleanup_expired()
        try:
            inspection = self._exporter.inspect(request)
        except TemplatePackageExportError as exc:
            raise self._export_app_error(exc) from exc
        operation_id = str(uuid4())
        now = datetime.now(UTC)
        token, token_hash = self._new_token()
        operation = TemplatePackageOperation(
            operation_id=operation_id,
            kind="EXPORT",
            status="READY",
            stage="READY_TO_EXPORT",
            progress_percent=60,
            message="書き出し内容を確認してください。",
            created_at=now,
            updated_at=now,
            expires_at=now + self.OPERATION_TTL,
            confirmation_token=None,
            inspection=inspection,
        )
        with self._lock:
            self._operations[operation_id] = operation
            self._tokens[token_hash] = _TokenRecord(
                token_hash=token_hash,
                purpose="EXPORT",
                operation_id=operation_id,
                expires_at=now + self.TOKEN_TTL,
                export_request=request,
                confirmation_digest=inspection.confirmation_digest,
            )
        return replace(operation, confirmation_token=token)

    def confirm_export(self, token: str) -> TemplatePackageExport:
        record, operation = self._token(token, "EXPORT")
        if record.export_request is None or record.confirmation_digest is None:
            raise self._token_error("TEMPLATE_PACKAGE_TOKEN_INVALID", "確認情報が不正です。")
        with self._lock:
            if record.consumed:
                raise self._token_error(
                    "TEMPLATE_PACKAGE_TOKEN_ALREADY_USED",
                    "書き出し確認tokenは既に使用されています。内容を再確認してください。",
                )
            record.consumed = True
            operation.status = "RUNNING"
            operation.stage = "BUILDING_PACKAGE"
            operation.progress_percent = 80
            operation.message = "template packageを書き出しています。"
            operation.confirmation_token = None
            operation.updated_at = datetime.now(UTC)
        try:
            exported = self._exporter.export(
                record.export_request,
                confirmed_inspection_digest=record.confirmation_digest,
                write_to_storage=False,
            )
        except TemplatePackageExportError as exc:
            self._fail(operation.operation_id, exc.code, exc.detail, self._category(exc.code))
            raise self._export_app_error(exc) from exc
        with self._lock:
            current = self._operations[operation.operation_id]
            current.status = "COMPLETED"
            current.stage = "COMPLETED"
            current.progress_percent = 100
            current.message = "template packageの書き出しが完了しました。"
            current.file_name = exported.file_name
            current.package_sha256 = hashlib.sha256(exported.content).hexdigest()
            current.updated_at = datetime.now(UTC)
        return exported

    def confirm_import(
        self,
        token: str,
        *,
        allow_older_version: bool,
    ) -> TemplatePackageOperation:
        record, operation = self._token(token, "IMPORT")
        with self._lock:
            if record.consumed:
                return replace(operation)
            record.consumed = True
            operation.confirmation_token = None
            operation.status = "RUNNING"
            operation.stage = "IMPORTING"
            operation.progress_percent = 70
            operation.message = "検証済みpackageを取り込んでいます。"
            operation.updated_at = datetime.now(UTC)
        try:
            content = self._verified_staged_content(
                operation.operation_id,
                record.package_sha256,
            )
        except AppError as exc:
            self._fail(operation.operation_id, exc.code, exc.detail, "SECURITY")
            raise
        self._executor.submit(
            self._run_import,
            operation.operation_id,
            content,
            record.package_sha256 or "",
            allow_older_version,
            record.import_source_type,
        )
        return self.operation(operation.operation_id)

    def operation(self, operation_id: str) -> TemplatePackageOperation:
        self._cleanup_expired()
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                raise AppError(
                    status=404,
                    code="TEMPLATE_PACKAGE_OPERATION_NOT_FOUND",
                    title="Template package operation not found",
                    detail="template package処理が見つかりません。最初からやり直してください。",
                )
            return replace(operation)

    def preview_asset(self, operation_id: str, role: str) -> tuple[bytes, str]:
        operation = self.operation(operation_id)
        if operation.kind != "IMPORT" or operation.status != "READY":
            raise AppError(
                status=409,
                code="TEMPLATE_PACKAGE_PREVIEW_NOT_AVAILABLE",
                title="Template package preview is not available",
                detail="検証済みの取り込みpackageだけをpreviewできます。",
            )
        paths = {
            "preview": "assets/preview.png",
            "clean-template": "assets/clean-template.png",
        }
        path = paths.get(role)
        if path is None:
            raise AppError(
                status=404,
                code="TEMPLATE_PACKAGE_PREVIEW_NOT_FOUND",
                title="Template package preview was not found",
                detail="指定したpreview画像はありません。",
            )
        content = self._verified_staged_content(
            operation_id,
            operation.package_sha256,
        )
        try:
            package = self._reader.read(content)
        except TemplatePackageImportError as exc:
            raise self._import_app_error(exc) from exc
        payload = package.payloads.get(path)
        if payload is None:
            raise AppError(
                status=404,
                code="TEMPLATE_PACKAGE_PREVIEW_NOT_FOUND",
                title="Template package preview was not found",
                detail="指定したpreview画像はありません。",
            )
        return payload, "image/png"

    def delete(self, operation_id: str) -> None:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is not None and operation.status == "RUNNING":
                raise AppError(
                    status=409,
                    code="TEMPLATE_PACKAGE_OPERATION_RUNNING",
                    title="Template package operation is running",
                    detail="処理中のoperationは削除できません。",
                )
            self._operations.pop(operation_id, None)
            stale_tokens = [
                key
                for key, item in self._tokens.items()
                if item.operation_id == operation_id
            ]
            for key in stale_tokens:
                self._tokens.pop(key, None)
        self._storage.delete_file(self._import_path(operation_id))

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run_import(
        self,
        operation_id: str,
        content: bytes,
        expected_sha256: str,
        allow_older_version: bool,
        source_type: Literal["FILE", "DRIVE"],
    ) -> None:
        try:
            result = self._importer.import_package(
                content,
                expected_package_sha256=expected_sha256,
                allow_older_version=allow_older_version,
                source_type=source_type,
            )
        except TemplatePackageImportError as exc:
            self._fail(operation_id, exc.code, exc.detail, self._category(exc.code))
        except Exception:
            self._fail(
                operation_id,
                "TEMPLATE_PACKAGE_IMPORT_FAILED",
                "template packageの取り込みに失敗しました。",
                "SYSTEM",
            )
        else:
            with self._lock:
                operation = self._operations.get(operation_id)
                if operation is None:
                    return
                operation.status = "COMPLETED"
                operation.stage = "COMPLETED"
                operation.progress_percent = 100
                operation.message = (
                    "既に取り込み済みの同一packageを確認しました。"
                    if not result.created
                    else "template packageを非有効版として取り込みました。"
                )
                operation.result = result
                operation.updated_at = datetime.now(UTC)
        finally:
            self._storage.delete_file(self._import_path(operation_id))

    def _verified_staged_content(self, operation_id: str, expected_sha256: str | None) -> bytes:
        relative_path = self._import_path(operation_id)
        try:
            content = self._storage.read_bytes(relative_path)
        except OSError as exc:
            raise AppError(
                status=410,
                code="TEMPLATE_PACKAGE_TEMPORARY_FILE_MISSING",
                title="Template package temporary file is missing",
                detail="一時保存したpackageが見つかりません。再度選択してください。",
            ) from exc
        actual = hashlib.sha256(content).hexdigest()
        if expected_sha256 is None or not secrets.compare_digest(actual, expected_sha256):
            self._storage.delete_file(relative_path)
            raise AppError(
                status=409,
                code="IMPORT_PACKAGE_CHANGED",
                title="Template package changed after inspection",
                detail="確認後にpackageが差し替えられたため取り込みを停止しました。",
            )
        return content

    def _read_bounded(self, source: BinaryIO) -> bytes:
        maximum = DEFAULT_TEMPLATE_PACKAGE_SECURITY_LIMITS.max_package_bytes
        chunks: list[bytes] = []
        total = 0
        while chunk := source.read(self.READ_CHUNK_BYTES):
            total += len(chunk)
            if total > maximum:
                raise AppError(
                    status=413,
                    code="PACKAGE_TOO_LARGE",
                    title="Template package is too large",
                    detail="template packageの容量上限を超えています。",
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _token(
        self,
        token: str,
        purpose: OperationKind,
    ) -> tuple[_TokenRecord, TemplatePackageOperation]:
        self._cleanup_expired()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            record = self._tokens.get(token_hash)
            if record is None or not secrets.compare_digest(record.token_hash, token_hash):
                raise self._token_error("TEMPLATE_PACKAGE_TOKEN_INVALID", "確認tokenが不正です。")
            if record.purpose != purpose:
                raise self._token_error(
                    "TEMPLATE_PACKAGE_TOKEN_INVALID",
                    "確認tokenの用途が不正です。",
                )
            if record.expires_at <= datetime.now(UTC):
                raise self._token_error(
                    "TEMPLATE_PACKAGE_TOKEN_EXPIRED",
                    "確認tokenの有効期限が切れました。内容を再確認してください。",
                    status=410,
                )
            operation = self._operations.get(record.operation_id)
            if operation is None:
                raise self._token_error("TEMPLATE_PACKAGE_TOKEN_INVALID", "確認対象がありません。")
            return record, operation

    def _cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        expired_operations: list[str] = []
        with self._lock:
            for operation_id, operation in self._operations.items():
                if operation.expires_at <= now and operation.status != "RUNNING":
                    expired_operations.append(operation_id)
            for operation_id in expired_operations:
                self._operations.pop(operation_id, None)
            expired_tokens = [
                key
                for key, item in self._tokens.items()
                if item.operation_id in expired_operations
            ]
            for key in expired_tokens:
                self._tokens.pop(key, None)
        for operation_id in expired_operations:
            self._storage.delete_file(self._import_path(operation_id))

    def _cleanup_stale_files(self) -> None:
        root = self._storage.resolve("tmp/template-package-inspections")
        root.mkdir(parents=True, exist_ok=True)
        threshold = datetime.now(UTC).timestamp() - self.OPERATION_TTL.total_seconds()
        for path in root.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < threshold:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _fail(
        self,
        operation_id: str,
        code: str,
        detail: str,
        category: ErrorCategory,
    ) -> None:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return
            operation.status = "FAILED"
            operation.stage = "FAILED"
            operation.progress_percent = 100
            operation.message = detail
            operation.error_code = code
            operation.error_message = detail
            operation.error_category = category
            operation.updated_at = datetime.now(UTC)

    @staticmethod
    def _new_token() -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        return token, hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _import_path(operation_id: str) -> str:
        return f"tmp/template-package-inspections/{operation_id}.llmocr-template"

    @staticmethod
    def _category(code: str) -> ErrorCategory:
        if code in {"PACKAGE_REQUIRED_FEATURE_UNSUPPORTED", "PACKAGE_APP_VERSION_UNSUPPORTED"}:
            return "COMPATIBILITY"
        if code.startswith("IMPORT_") or code.endswith("_CONFLICT"):
            return "CONFLICT"
        if code.startswith("PACKAGE_"):
            return "SECURITY"
        return "VALIDATION"

    @classmethod
    def _import_app_error(cls, exc: TemplatePackageImportError) -> AppError:
        category = cls._category(exc.code)
        status = 409 if category in {"COMPATIBILITY", "CONFLICT"} else 422
        if exc.code == "PACKAGE_TOO_LARGE":
            status = 413
        return AppError(
            status=status,
            code=exc.code,
            title="Template package import rejected",
            detail=exc.detail,
            metadata={"errorCategory": category},
        )

    @classmethod
    def _export_app_error(cls, exc: TemplatePackageExportError) -> AppError:
        category = cls._category(exc.code)
        return AppError(
            status=409 if category == "CONFLICT" else 422,
            code=exc.code,
            title="Template package export rejected",
            detail=exc.detail,
            metadata={"errorCategory": category},
        )

    @staticmethod
    def _token_error(code: str, detail: str, *, status: int = 409) -> AppError:
        return AppError(
            status=status,
            code=code,
            title="Template package confirmation token rejected",
            detail=detail,
        )
