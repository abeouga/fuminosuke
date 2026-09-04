from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    ConfirmedResult,
    DocumentFolder,
    DocumentFolderDriveMapping,
    DocumentLibrarySyncState,
    GoogleOAuthAccount,
    LedgerDocument,
    SpreadsheetOutputOperation,
    Template,
    TemplateOutputFolder,
)
from app.db.session import Database

_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD_FILES = "https://www.googleapis.com/upload/drive/v3/files"
_DRIVE_CHANGES = "https://www.googleapis.com/drive/v3/changes"
_SHEETS_SPREADSHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ZIP_MIME = "application/zip"
_TEMPLATE_PACKAGE_MIME = "application/vnd.llmocr.template+zip"
_WORKSPACE_ROOT_NAME = "LLM OCR ワークスペース"
_WORKSPACE_ROOT_MARKER = "v1"
_TEMPLATE_ROOT_NAME = "LLM OCR テンプレート"
_TEMPLATE_ROOT_MARKER = "v1"
_TEMPLATE_FILE_FIELDS = (
    "id,name,mimeType,parents,createdTime,modifiedTime,size,trashed,"
    "explicitlyTrashed,version,headRevisionId,sha256Checksum,appProperties"
)


class GoogleDriveService:
    def __init__(
        self,
        database: Database,
        credentials_provider: Callable[[str | None], Credentials],
        subject_provider: Callable[[], str | None],
        picker_api_key: str | None = None,
        picker_app_id: str | None = None,
        picker_project_matches: bool = True,
        picker_configuration_error: str | None = None,
    ) -> None:
        self._database = database
        self._credentials_provider = credentials_provider
        self._subject_provider = subject_provider
        self._picker_api_key = picker_api_key
        self._picker_app_id = picker_app_id
        self._picker_project_matches = picker_project_matches
        self._picker_configuration_error = picker_configuration_error
        self._session: AuthorizedSession | None = None
        self._session_subject: str | None = None
        self._request_subject: str | None = None
        self._lock = Lock()

    def reset_authorization(self) -> None:
        with self._lock:
            self._session = None
            self._session_subject = None

    @contextmanager
    def authorization_change(self) -> Iterator[None]:
        """Serialize active-account changes with Drive operations."""
        with self._lock:
            self._session = None
            self._session_subject = None
            try:
                yield
            finally:
                self._session = None
                self._session_subject = None

    @contextmanager
    def _drive_operation(
        self,
        expected_subject: str | None = None,
    ) -> Iterator[str]:
        with self._lock:
            subject = self._require_subject(expected_subject)
            self._request_subject = subject
            try:
                yield subject
            finally:
                self._request_subject = None

    def output_folder(self, expected_subject: str | None = None) -> dict[str, Any]:
        subject = self._require_subject(expected_subject)
        snapshot = self._account_snapshot(subject)
        with self._database.session() as session:
            roots = self._library_roots(session, subject)
        selected_id = snapshot["selectedId"] or (roots[0][0] if roots else None)
        selected_name = snapshot["selectedName"] or (roots[0][1] if roots else None)
        return {
            "pickerConfigured": bool(
                self._picker_api_key
                and self._picker_app_id
                and self._picker_project_matches
            ),
            "pickerConfigurationError": self._picker_configuration_error,
            "folderId": selected_id,
            "folderName": selected_name,
            "roots": [
                {"folderId": folder_id, "folderName": folder_name or folder_id}
                for folder_id, folder_name in roots
            ],
        }

    def workspace_remote_state(
        self,
        *,
        device_id: str,
        device_name: str,
        acquire_lease: bool,
        lease_seconds: int = 120,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            root = self._workspace_root(subject)
            properties = self._workspace_properties(root)
            now = datetime.now(UTC)
            owner_id = properties.get("llmOcrLeaseDeviceId") or None
            owner_name = properties.get("llmOcrLeaseDeviceName") or None
            lease_expires_at = self._drive_datetime(
                properties.get("llmOcrLeaseExpiresAt")
            )
            lease_active = bool(lease_expires_at and lease_expires_at > now)
            can_acquire = not lease_active or owner_id == device_id
            if acquire_lease and can_acquire:
                lease_expires_at = now + timedelta(seconds=max(30, lease_seconds))
                properties.update(
                    {
                        "llmOcrWorkspaceRoot": _WORKSPACE_ROOT_MARKER,
                        "llmOcrLeaseDeviceId": device_id,
                        "llmOcrLeaseDeviceName": device_name[:120],
                        "llmOcrLeaseExpiresAt": lease_expires_at.isoformat(),
                    }
                )
                root = self._patch_workspace_properties(root, properties)
                verified = self._workspace_properties(root)
                owner_id = verified.get("llmOcrLeaseDeviceId") or None
                owner_name = verified.get("llmOcrLeaseDeviceName") or None
                lease_expires_at = self._drive_datetime(
                    verified.get("llmOcrLeaseExpiresAt")
                )
                lease_active = bool(lease_expires_at and lease_expires_at > now)
            snapshots = self._workspace_snapshots(root)
            return {
                "googleSubject": subject,
                "rootFolderId": str(root["id"]),
                "leaseOwnerDeviceId": owner_id,
                "leaseOwnerDeviceName": owner_name,
                "leaseExpiresAt": lease_expires_at,
                "leaseOwned": bool(
                    lease_active and owner_id == device_id
                ),
                "snapshots": snapshots,
            }

    def upload_workspace_snapshot(
        self,
        *,
        archive_path: Path,
        revision: int,
        sha256: str,
        device_id: str,
        created_at: datetime,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            root = self._workspace_root(subject)
            metadata = {
                "name": created_at.astimezone().strftime(
                    f"llm-ocr-workspace-r{revision:06d}-%Y%m%d-%H%M%S.zip"
                ),
                "mimeType": _ZIP_MIME,
                "parents": [str(root["id"])],
                "appProperties": {
                    "llmOcrWorkspaceSnapshot": _WORKSPACE_ROOT_MARKER,
                    "llmOcrWorkspaceRevision": str(revision),
                    "llmOcrWorkspaceSha256": sha256,
                    "llmOcrWorkspaceDeviceId": device_id,
                    "llmOcrWorkspaceCreatedAt": created_at.isoformat(),
                    "llmOcrWorkspaceReady": "true",
                },
            }
            response = self._raw_drive_request(
                "POST",
                _DRIVE_UPLOAD_FILES,
                params={
                    "uploadType": "resumable",
                    "fields": (
                        "id,name,mimeType,parents,createdTime,modifiedTime,"
                        "size,appProperties"
                    ),
                    "supportsAllDrives": "true",
                },
                headers={
                    "X-Upload-Content-Type": _ZIP_MIME,
                    "X-Upload-Content-Length": str(archive_path.stat().st_size),
                },
                json=metadata,
            )
            upload_url = response.headers.get("Location")
            if not upload_url:
                raise AppError(
                    status=502,
                    code="WORKSPACE_UPLOAD_SESSION_MISSING",
                    title="Workspace upload session was not created",
                    detail="Google Driveへの同期用アップロードを開始できませんでした。",
                )
            with archive_path.open("rb") as archive:
                completed = self._raw_drive_request(
                    "PUT",
                    upload_url,
                    headers={
                        "Content-Type": _ZIP_MIME,
                        "Content-Length": str(archive_path.stat().st_size),
                    },
                    data=archive,
                    timeout=600,
                )
            payload = completed.json()
            if not isinstance(payload, dict) or not payload.get("id"):
                raise AppError(
                    status=502,
                    code="WORKSPACE_UPLOAD_FAILED",
                    title="Workspace snapshot upload failed",
                    detail="Google Driveへ同期用バックアップを書き込めませんでした。",
                )
            verified = self._drive_item(str(payload["id"]))
            if not self._is_workspace_snapshot(verified, root_id=str(root["id"])):
                raise AppError(
                    status=409,
                    code="WORKSPACE_UPLOAD_VERIFICATION_FAILED",
                    title="Workspace snapshot could not be verified",
                    detail="Google Driveへ書き込んだ同期用バックアップを確認できませんでした。",
                )
            return self._workspace_snapshot_view(verified or {})

    def download_workspace_snapshot(
        self,
        file_id: str,
        destination: Path,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            root = self._workspace_root(subject)
            item = self._drive_item(file_id)
            if not self._is_workspace_snapshot(item, root_id=str(root["id"])):
                raise AppError(
                    status=404,
                    code="WORKSPACE_SNAPSHOT_NOT_FOUND",
                    title="Workspace snapshot was not found",
                    detail="選択したGoogle Drive同期履歴が見つかりません。",
                )
            response = self._raw_drive_request(
                "GET",
                f"{_DRIVE_FILES}/{quote(file_id, safe='')}",
                params={"alt": "media", "supportsAllDrives": "true"},
                stream=True,
                timeout=600,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as target:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        target.write(chunk)
            return self._workspace_snapshot_view(item or {})

    def trim_workspace_snapshots(
        self,
        *,
        retain: int = 5,
        expected_subject: str | None = None,
    ) -> None:
        with self._drive_operation(expected_subject) as subject:
            root = self._workspace_root(subject)
            snapshots = self._workspace_snapshots(root)
            for item in snapshots[max(1, retain):]:
                self._request(
                    "PATCH",
                    f"{_DRIVE_FILES}/{quote(str(item['fileId']), safe='')}",
                    params={"fields": "id,trashed", "supportsAllDrives": "true"},
                    json={"trashed": True},
                )

    def release_workspace_lease(
        self,
        device_id: str,
        expected_subject: str | None = None,
    ) -> None:
        with self._drive_operation(expected_subject) as subject:
            root = self._workspace_root(subject)
            properties = self._workspace_properties(root)
            if properties.get("llmOcrLeaseDeviceId") != device_id:
                return
            properties.update(
                {
                    "llmOcrWorkspaceRoot": _WORKSPACE_ROOT_MARKER,
                    "llmOcrLeaseDeviceId": "",
                    "llmOcrLeaseDeviceName": "",
                    "llmOcrLeaseExpiresAt": "",
                }
            )
            self._patch_workspace_properties(root, properties)

    def template_package_files(
        self,
        expected_subject: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._drive_operation(expected_subject) as subject:
            root = self._template_package_root(subject)
            root_id = str(root["id"])
            items = self._find_template_package_files(root_id)
            return [
                self._template_package_file_view(item)
                for item in items
                if self._is_template_package_file(item, root_id=root_id)
            ]

    def upload_template_package_release(
        self,
        *,
        content: bytes,
        publisher_id: str,
        package_id: str,
        release_version: str,
        content_digest: str,
        package_sha256: str,
        file_name: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            root = self._template_package_root(subject)
            root_id = str(root["id"])
            folders = self._template_package_identity_folders(
                root_id=root_id,
                publisher_id=publisher_id,
                package_id=package_id,
            )
            publisher_hash = self._identity_hash(publisher_id)
            package_hash = self._identity_hash(package_id)
            matches = [
                item
                for item in self._find_template_package_files(root_id)
                if self._template_properties(item).get("lok") == "release"
                and self._template_properties(item).get("lop") == publisher_hash
                and self._template_properties(item).get("loi") == package_hash
                and self._template_properties(item).get("lov") == release_version
            ]
            if matches:
                if len(matches) != 1:
                    raise self._template_release_conflict(
                        "同一identity/versionのDrive releaseが複数存在します。"
                    )
                existing = matches[0]
                properties = self._template_properties(existing)
                if (
                    properties.get("lod") != content_digest
                    or properties.get("los") != package_sha256
                ):
                    raise self._template_release_conflict(
                        "同一identity/versionに異なるcontent digestが存在します。"
                    )
                return self._template_package_file_view(existing)

            properties = self._template_file_properties(
                kind="release",
                root_id=root_id,
                folder_id=folders["releases"],
                publisher_id=publisher_id,
                package_id=package_id,
                release_version=release_version,
                content_digest=content_digest,
                package_sha256=package_sha256,
            )
            uploaded = self._upload_template_package(
                method="POST",
                file_id=None,
                content=content,
                metadata={
                    "name": file_name,
                    "mimeType": _TEMPLATE_PACKAGE_MIME,
                    "parents": [folders["releases"]],
                    "appProperties": properties,
                },
                etag=None,
            )
            verified = self._drive_item_with_etag(str(uploaded.get("id") or ""))
            if not self._is_template_package_file(verified, root_id=root_id, kind="release"):
                raise AppError(
                    status=409,
                    code="DRIVE_TEMPLATE_RELEASE_VERIFICATION_FAILED",
                    title="Drive template release verification failed",
                    detail="Google Driveへ保存した正式版packageを確認できませんでした。",
                )
            return self._template_package_file_view(verified)

    def upload_template_package_draft(
        self,
        *,
        content: bytes,
        publisher_id: str,
        package_id: str,
        release_version: str,
        content_digest: str,
        package_sha256: str,
        file_name: str,
        draft_id: str,
        base_release_version: str | None,
        expected_revision: int | None,
        expected_drive_file_id: str | None,
        expected_drive_version: str | None,
        expected_head_revision_id: str | None,
        expected_etag: str | None,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            root = self._template_package_root(subject)
            root_id = str(root["id"])
            folders = self._template_package_identity_folders(
                root_id=root_id,
                publisher_id=publisher_id,
                package_id=package_id,
            )
            if expected_drive_file_id is None:
                revision = 1
                properties = self._template_file_properties(
                    kind="draft",
                    root_id=root_id,
                    folder_id=folders["drafts"],
                    publisher_id=publisher_id,
                    package_id=package_id,
                    release_version=release_version,
                    content_digest=content_digest,
                    package_sha256=package_sha256,
                    draft_id=draft_id,
                    draft_revision=revision,
                    base_release_version=base_release_version,
                )
                uploaded = self._upload_template_package(
                    method="POST",
                    file_id=None,
                    content=content,
                    metadata={
                        "name": file_name,
                        "mimeType": _TEMPLATE_PACKAGE_MIME,
                        "parents": [folders["drafts"]],
                        "appProperties": properties,
                    },
                    etag=None,
                )
            else:
                current = self._drive_item_with_etag(expected_drive_file_id)
                current_properties = self._template_properties(current)
                if (
                    current_properties.get("lou") == draft_id
                    and current_properties.get("lod") == content_digest
                    and current_properties.get("los") == package_sha256
                ):
                    return self._template_package_file_view(current)
                self._require_matching_draft(
                    current,
                    root_id=root_id,
                    draft_id=draft_id,
                    expected_revision=expected_revision,
                    expected_drive_version=expected_drive_version,
                    expected_head_revision_id=expected_head_revision_id,
                    expected_etag=expected_etag,
                )
                if not expected_etag:
                    raise AppError(
                        status=409,
                        code="DRIVE_TEMPLATE_DRAFT_PRECONDITION_UNAVAILABLE",
                        title="Drive draft precondition is unavailable",
                        detail="ETagを確認できないため作業版を安全に更新できません。",
                    )
                revision = int(current_properties["lor"]) + 1
                properties = self._template_file_properties(
                    kind="draft",
                    root_id=root_id,
                    folder_id=folders["drafts"],
                    publisher_id=publisher_id,
                    package_id=package_id,
                    release_version=release_version,
                    content_digest=content_digest,
                    package_sha256=package_sha256,
                    draft_id=draft_id,
                    draft_revision=revision,
                    base_release_version=base_release_version,
                )
                uploaded = self._upload_template_package(
                    method="PATCH",
                    file_id=expected_drive_file_id,
                    content=content,
                    metadata={
                        "name": file_name,
                        "mimeType": _TEMPLATE_PACKAGE_MIME,
                        "appProperties": properties,
                    },
                    etag=expected_etag,
                )
            verified = self._drive_item_with_etag(str(uploaded.get("id") or ""))
            if not self._is_template_package_file(verified, root_id=root_id, kind="draft"):
                raise AppError(
                    status=409,
                    code="DRIVE_TEMPLATE_DRAFT_VERIFICATION_FAILED",
                    title="Drive template draft verification failed",
                    detail="Google Driveへ保存した作業版packageを確認できませんでした。",
                )
            verified_properties = self._template_properties(verified)
            if (
                verified_properties.get("lod") != content_digest
                or verified_properties.get("los") != package_sha256
                or verified_properties.get("lor") != str(revision)
            ):
                raise self._template_draft_conflict()
            return self._template_package_file_view(verified)

    def download_template_package_file(
        self,
        file_id: str,
        *,
        expected_kind: str,
        expected_subject: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        with self._drive_operation(expected_subject) as subject:
            root = self._template_package_root(subject)
            root_id = str(root["id"])
            item = self._drive_item_with_etag(file_id)
            if not self._is_template_package_file(
                item,
                root_id=root_id,
                kind=expected_kind,
            ):
                raise AppError(
                    status=404,
                    code="DRIVE_TEMPLATE_PACKAGE_NOT_FOUND",
                    title="Drive template package was not found",
                    detail="選択したGoogle Driveのtemplate packageが見つかりません。",
                )
            response = self._raw_drive_request(
                "GET",
                f"{_DRIVE_FILES}/{quote(file_id, safe='')}",
                params={"alt": "media", "supportsAllDrives": "true"},
                stream=True,
                timeout=600,
            )
            content = b"".join(
                chunk
                for chunk in response.iter_content(chunk_size=1024 * 1024)
                if chunk
            )
            return content, self._template_package_file_view(item)

    def picker_session(self, expected_subject: str | None = None) -> dict[str, str]:
        if not self._picker_api_key or not self._picker_app_id:
            raise AppError(
                status=503,
                code="GOOGLE_PICKER_NOT_CONFIGURED",
                title="Google Picker is not configured",
                detail="Google Picker APIキーとApp IDが設定されていません。",
            )
        if not self._picker_project_matches:
            raise AppError(
                status=503,
                code="GOOGLE_PICKER_PROJECT_MISMATCH",
                title="Google Picker project does not match OAuth",
                detail=(
                    self._picker_configuration_error
                    or "PickerとOAuthクライアントのGoogle Cloudプロジェクトが一致しません。"
                ),
            )
        with self._drive_operation(expected_subject) as subject:
            credentials = self._credentials_provider(subject)
            try:
                credentials.refresh(GoogleAuthRequest())  # type: ignore[no-untyped-call]
            except (GoogleAuthError, RuntimeError) as exc:
                raise AppError(
                    status=401,
                    code="GOOGLE_AUTH_REFRESH_FAILED",
                    title="Google authorization refresh failed",
                    detail="Google連携を更新できませんでした。連携をやり直してください。",
                ) from exc
            if not credentials.token:
                raise AppError(
                    status=502,
                    code="GOOGLE_PICKER_TOKEN_MISSING",
                    title="Google Picker token is missing",
                    detail="Google Pickerを開くための一時トークンを取得できませんでした。",
                )
            return {
                "accessToken": credentials.token,
                "developerKey": self._picker_api_key,
                "appId": self._picker_app_id,
            }

    def select_output_folder(
        self,
        folder_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            item = self._request(
                "GET",
                f"{_DRIVE_FILES}/{quote(folder_id, safe='')}",
                params={
                    "fields": "id,name,mimeType,trashed,capabilities(canAddChildren)",
                    "supportsAllDrives": "true",
                },
            )
            if item.get("mimeType") != _FOLDER_MIME or item.get("trashed") is True:
                raise AppError(
                    status=422,
                    code="DRIVE_FOLDER_INVALID",
                    title="Drive folder is invalid",
                    detail="選択した保存先は利用可能なGoogle Driveフォルダーではありません。",
                )
            capabilities = item.get("capabilities")
            if isinstance(capabilities, dict) and capabilities.get("canAddChildren") is False:
                raise AppError(
                    status=403,
                    code="DRIVE_FOLDER_NOT_WRITABLE",
                    title="Drive folder is not writable",
                    detail="選択したフォルダーにはファイルを追加できません。",
                )
            name = str(item.get("name") or "選択したフォルダー")
            with self._database.session() as session:
                account = session.get(GoogleOAuthAccount, subject)
                if account is None:
                    raise self._not_connected()
                account.drive_root_folder_id = folder_id
                account.drive_root_folder_name = name
                account.default_output_folder_id = folder_id
                account.default_output_folder_name = name
                account.default_output_folder_path = name
                session.commit()
            return self.output_folder(subject)

    def template_output_folder(
        self,
        template_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        subject = self._require_subject(expected_subject)
        with self._database.session() as session:
            template = session.get(Template, template_id)
            if template is None:
                raise AppError(
                    status=404,
                    code="TEMPLATE_NOT_FOUND",
                    title="Template not found",
                    detail="指定されたテンプレートが見つかりません。",
                )
            account = self._active_account(session, subject)
            selected = session.get(
                TemplateOutputFolder,
                (account.google_subject, template_id),
            )
            return {
                "templateFolderId": selected.folder_id if selected else None,
                "templateFolderName": selected.folder_name if selected else None,
                "effectiveFolderId": (
                    selected.folder_id if selected else account.default_output_folder_id
                ),
                "effectiveFolderName": (
                    selected.folder_name if selected else account.default_output_folder_name
                ),
                "usesDefault": selected is None,
            }

    def select_template_output_folder(
        self,
        template_id: str,
        folder_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            item = self._request(
                "GET",
                f"{_DRIVE_FILES}/{quote(folder_id, safe='')}",
                params={
                    "fields": "id,name,mimeType,trashed,capabilities(canAddChildren)",
                    "supportsAllDrives": "true",
                },
            )
            if item.get("mimeType") != _FOLDER_MIME or item.get("trashed") is True:
                raise AppError(
                    status=422,
                    code="DRIVE_FOLDER_INVALID",
                    title="Drive folder is invalid",
                    detail="選択した保存先は利用可能なGoogle Driveフォルダーではありません。",
                )
            capabilities = item.get("capabilities")
            if isinstance(capabilities, dict) and capabilities.get("canAddChildren") is False:
                raise AppError(
                    status=403,
                    code="DRIVE_FOLDER_NOT_WRITABLE",
                    title="Drive folder is not writable",
                    detail="選択したフォルダーにはファイルを追加できません。",
                )
            name = str(item.get("name") or "選択したフォルダー")
            with self._database.session() as session:
                template = session.get(Template, template_id)
                if template is None:
                    raise AppError(
                        status=404,
                        code="TEMPLATE_NOT_FOUND",
                        title="Template not found",
                        detail="指定されたテンプレートが見つかりません。",
                    )
                account = self._active_account(session, subject)
                selected = session.get(
                    TemplateOutputFolder,
                    (account.google_subject, template_id),
                )
                if selected is None:
                    selected = TemplateOutputFolder(
                        google_subject=account.google_subject,
                        template_id=template_id,
                        folder_id=folder_id,
                        folder_name=name,
                        folder_path=name,
                    )
                    session.add(selected)
                else:
                    selected.folder_id = folder_id
                    selected.folder_name = name
                    selected.folder_path = name
                session.commit()
            return self.template_output_folder(template_id, subject)

    def document_target(
        self,
        document_id: str | None,
        expected_subject: str | None = None,
    ) -> tuple[str | None, str | None]:
        if not document_id:
            return None, None
        with (
            self._drive_operation(expected_subject) as subject,
            self._database.session() as session,
        ):
            ledger = session.get(LedgerDocument, document_id)
            if ledger is None:
                return None, None
            self._assert_ledger_spreadsheet_in_current_roots(session, ledger, subject)
            session.commit()
            return ledger.spreadsheet_id, ledger.spreadsheet_url

    def spreadsheet_targets(
        self,
        expected_subject: str | None = None,
    ) -> list[tuple[str, str]]:
        subject = self._require_subject(expected_subject)
        with self._database.session() as session:
            rows = session.execute(
                select(LedgerDocument.spreadsheet_id, LedgerDocument.spreadsheet_url).where(
                    LedgerDocument.spreadsheet_id.is_not(None),
                    LedgerDocument.drive_google_subject == subject,
                )
            ).all()
            return [
                (spreadsheet_id, spreadsheet_url or self._spreadsheet_url(spreadsheet_id))
                for spreadsheet_id, spreadsheet_url in rows
                if isinstance(spreadsheet_id, str)
            ]

    def confirm_legacy_document_ownership(
        self,
        document_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        with self._drive_operation(expected_subject) as subject:
            with self._database.session() as session:
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise AppError(
                        status=404,
                        code="DOCUMENT_NOT_FOUND",
                        title="Document was not found",
                        detail="書類が見つかりません。",
                    )
                if not ledger.spreadsheet_id:
                    raise AppError(
                        status=409,
                        code="DOCUMENT_SPREADSHEET_NOT_READY",
                        title="Document spreadsheet is not ready",
                        detail="この書類には所有者を確認できるスプレッドシートがありません。",
                    )
                if ledger.drive_google_subject is not None:
                    if ledger.drive_google_subject != subject:
                        raise self._drive_document_account_mismatch()
                    return {
                        "documentId": ledger.id,
                        "googleSubject": subject,
                        "driveRootFolderId": ledger.drive_root_folder_id,
                        "alreadyConfirmed": True,
                        "locationAlignedFromLibrary": False,
                    }
                spreadsheet_id = ledger.spreadsheet_id
                roots = dict(self._library_roots(session, subject))

            item = self._drive_item(spreadsheet_id)
            parent_id = self._validated_legacy_ownership_item(item, document_id)
            align_from_library = False
            target_parent_id = parent_id
            with self._database.session() as session:
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.spreadsheet_id != spreadsheet_id:
                    raise AppError(
                        status=409,
                        code="DOCUMENT_CHANGED",
                        title="Document changed",
                        detail="確認中に書類が変更されました。再同期してください。",
                    )
                matched_root_id, _matched_folder_id = self._legacy_ownership_location(
                    session,
                    subject,
                    parent_id,
                    roots,
                )
                if (
                    ledger.library_folder_id is not None
                    and _matched_folder_id is None
                    and parent_id == matched_root_id
                ):
                    local_folder = session.get(
                        DocumentFolder,
                        ledger.library_folder_id,
                    )
                    if local_folder is not None:
                        mapping, _created = self._ensure_library_folder_mapping(
                            session,
                            subject,
                            local_folder,
                            matched_root_id,
                        )
                        target_parent_id = mapping.drive_folder_id
                        align_from_library = target_parent_id != parent_id
                        session.commit()

            existing_properties = item.get("appProperties") if item is not None else None
            updated_properties = (
                dict(existing_properties) if isinstance(existing_properties, dict) else {}
            )
            updated_properties["llmOcrLedgerDocumentId"] = document_id
            updated_properties["llmOcrDriveRootId"] = matched_root_id
            patched = self._request(
                "PATCH",
                f"{_DRIVE_FILES}/{quote(spreadsheet_id, safe='')}",
                params={
                    "fields": (
                        "id,name,mimeType,parents,modifiedTime,trashed,"
                        "explicitlyTrashed,appProperties"
                    ),
                    "supportsAllDrives": "true",
                },
                json={"appProperties": updated_properties},
            )
            patched_parent_id = self._validated_legacy_ownership_item(
                patched,
                document_id,
                require_canonical=True,
            )
            if patched_parent_id != parent_id:
                raise AppError(
                    status=409,
                    code="DOCUMENT_CHANGED",
                    title="Document changed",
                    detail="確認中に保存場所が変更されました。再同期してください。",
                )
            if align_from_library:
                patched = self._move_drive_item(
                    spreadsheet_id,
                    target_parent_id,
                    [patched_parent_id],
                )
                moved_parent_ids = self._string_list(patched.get("parents"))
                if moved_parent_ids != [target_parent_id]:
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                        title="Drive document location changed",
                        detail=(
                            "所有者確認後にGoogle Drive側の保存場所を"
                            "独自ライブラリへ合わせられませんでした。"
                        ),
                    )
            final_item = self._drive_item(spreadsheet_id)
            final_parent_id = self._validated_legacy_ownership_item(
                final_item,
                document_id,
                require_canonical=True,
            )

            with self._database.session() as session:
                ledger = session.get(LedgerDocument, document_id)
                if (
                    ledger is None
                    or ledger.library_deleted_at is not None
                    or ledger.spreadsheet_id != spreadsheet_id
                ):
                    raise AppError(
                        status=409,
                        code="DOCUMENT_CHANGED",
                        title="Document changed",
                        detail="確認中に書類が変更されました。再同期してください。",
                    )
                if ledger.drive_google_subject is not None:
                    if ledger.drive_google_subject != subject:
                        raise self._drive_document_account_mismatch()
                    return {
                        "documentId": ledger.id,
                        "googleSubject": subject,
                        "driveRootFolderId": ledger.drive_root_folder_id,
                        "alreadyConfirmed": True,
                        "locationAlignedFromLibrary": False,
                    }
                current_roots = dict(self._library_roots(session, subject))
                matched_root_id, matched_folder_id = self._legacy_ownership_location(
                    session,
                    subject,
                    final_parent_id,
                    current_roots,
                )
                if ledger.library_folder_id != matched_folder_id:
                    ledger.library_folder_id = matched_folder_id
                    ledger.library_location_version += 1
                ledger.drive_google_subject = subject
                ledger.drive_root_folder_id = matched_root_id
                ledger.drive_folder_id = final_parent_id
                ledger.drive_folder_path = (
                    self._library_folder_path(session, matched_folder_id)
                    or current_roots.get(matched_root_id)
                )
                ledger.drive_ownership_confirmed_at = datetime.now(UTC)
                ledger.drive_ownership_confirmation_source = "USER_CONFIRMED"
                session.commit()
                return {
                    "documentId": ledger.id,
                    "googleSubject": subject,
                    "driveRootFolderId": matched_root_id,
                    "alreadyConfirmed": False,
                    "locationAlignedFromLibrary": align_from_library,
                }

    def _validated_legacy_ownership_item(
        self,
        item: dict[str, Any] | None,
        document_id: str,
        *,
        require_canonical: bool = False,
    ) -> str:
        if (
            item is None
            or item.get("trashed") is True
            or item.get("mimeType") != _SPREADSHEET_MIME
        ):
            raise AppError(
                status=409,
                code="DRIVE_SPREADSHEET_NOT_FOUND",
                title="Drive spreadsheet was not found",
                detail="現在のGoogleアカウントから対象スプレッドシートを確認できません。",
            )
        parents = self._string_list(item.get("parents"))
        if len(parents) != 1:
            raise AppError(
                status=409,
                code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                title="Drive document location is ambiguous",
                detail="対象スプレッドシートの保存場所を一意に確認できません。",
            )
        properties = item.get("appProperties")
        properties = properties if isinstance(properties, dict) else {}
        canonical = properties.get("llmOcrLedgerDocumentId")
        legacy = properties.get("llmOcrDocumentId")
        if (
            (canonical is not None and canonical != document_id)
            or (legacy is not None and legacy != document_id)
            or (require_canonical and canonical != document_id)
        ):
            raise AppError(
                status=409,
                code="DRIVE_DOCUMENT_IDENTITY_CONFLICT",
                title="Drive document identity conflicts",
                detail="対象スプレッドシートは別の書類IDとして登録されています。",
            )
        return parents[0]

    def _legacy_ownership_location(
        self,
        session: Session,
        subject: str,
        parent_id: str,
        roots: dict[str, str | None],
    ) -> tuple[str, str | None]:
        matched_root_id = parent_id if parent_id in roots else None
        matched_folder_id: str | None = None
        if matched_root_id is None:
            mapping = session.scalar(
                select(DocumentFolderDriveMapping).where(
                    DocumentFolderDriveMapping.google_subject == subject,
                    DocumentFolderDriveMapping.drive_folder_id == parent_id,
                    DocumentFolderDriveMapping.drive_root_folder_id.in_(set(roots)),
                )
            )
            if mapping is not None:
                matched_root_id = mapping.drive_root_folder_id
                matched_folder_id = mapping.document_folder_id
                if not self._mapped_drive_parent_chain_is_current(
                    session,
                    subject,
                    parent_id,
                    matched_root_id,
                ):
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                        title="Drive document location changed",
                        detail=(
                            "Google Drive側の親フォルダが未同期の状態です。"
                            "再同期してから所有者を確認してください。"
                        ),
                    )
        if matched_root_id is None:
            raise AppError(
                status=409,
                code="DRIVE_DOCUMENT_OUTSIDE_CONFIGURED_ROOT",
                title="Drive document is outside configured roots",
                detail=(
                    "対象スプレッドシートは現在設定されている保存先の外にあります。"
                    "保存先へ移動してから再同期してください。"
                ),
            )
        return matched_root_id, matched_folder_id

    def _mapped_drive_parent_chain_is_current(
        self,
        session: Session,
        subject: str,
        parent_id: str,
        root_id: str,
    ) -> bool:
        current_id = parent_id
        visited: set[str] = set()
        while current_id != root_id:
            if current_id in visited:
                return False
            visited.add(current_id)
            mapping = session.scalar(
                select(DocumentFolderDriveMapping).where(
                    DocumentFolderDriveMapping.google_subject == subject,
                    DocumentFolderDriveMapping.drive_folder_id == current_id,
                    DocumentFolderDriveMapping.drive_root_folder_id == root_id,
                )
            )
            if mapping is None:
                return False
            item = self._drive_item(current_id)
            if (
                item is None
                or item.get("trashed") is True
                or str(item.get("name") or "") != mapping.drive_name
                or self._string_list(item.get("parents"))
                != [mapping.drive_parent_folder_id]
            ):
                return False
            current_id = mapping.drive_parent_folder_id
        return True

    def create_document_spreadsheet(
        self,
        document_id: str,
        fallback_title: str,
        expected_subject: str | None = None,
        source_job_id: str | None = None,
        source_revision: int | None = None,
    ) -> tuple[str, str]:
        operation_key = f"document-create:{document_id}"[:124]
        with self._drive_operation(expected_subject) as subject:
            with self._database.session() as session:
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise AppError(
                        status=404,
                        code="LEDGER_DOCUMENT_NOT_FOUND",
                        title="Ledger document was not found",
                        detail="スプレッドシートに対応する台帳が見つかりません。",
                    )
                if ledger.spreadsheet_id:
                    self._assert_ledger_spreadsheet_in_current_roots(
                        session,
                        ledger,
                        subject,
                    )
                    session.commit()
                    return (
                        ledger.spreadsheet_id,
                        ledger.spreadsheet_url or self._spreadsheet_url(ledger.spreadsheet_id),
                    )
                resolved_job_id = source_job_id or ledger.current_job_id or document_id
                if source_job_id is not None and ledger.current_job_id != source_job_id:
                    raise self._spreadsheet_output_stale()
                confirmed = (
                    session.scalar(
                        select(ConfirmedResult).where(
                            ConfirmedResult.job_id == resolved_job_id
                        )
                    )
                    if resolved_job_id
                    else None
                )
                if (
                    source_revision is not None
                    and confirmed is not None
                    and confirmed.ocr_result_revision != source_revision
                ):
                    raise self._spreadsheet_output_stale()
                resolved_revision = (
                    source_revision
                    if source_revision is not None
                    else confirmed.ocr_result_revision
                    if confirmed is not None
                    else 0
                )
                account = self._active_account(session, subject)
                google_subject = account.google_subject
                if (
                    ledger.drive_google_subject is not None
                    and ledger.drive_google_subject != google_subject
                ):
                    raise self._drive_document_account_mismatch()
                template_folder = session.get(
                    TemplateOutputFolder,
                    (account.google_subject, ledger.template_id),
                )
                folder_id = (
                    template_folder.folder_id
                    if template_folder is not None
                    else account.default_output_folder_id
                )
                folder_path = (
                    template_folder.folder_path or template_folder.folder_name
                    if template_folder is not None
                    else account.default_output_folder_path or account.default_output_folder_name
                )
                if not folder_id:
                    raise AppError(
                        status=409,
                        code="DRIVE_OUTPUT_FOLDER_REQUIRED",
                        title="Drive output folder is required",
                        detail="先に保存先設定でGoogle Driveフォルダーを選択してください。",
                    )
                parent_folder_id = folder_id
                library_path = self._library_folder_path(
                    session,
                    ledger.library_folder_id,
                )
                if ledger.library_folder_id is not None:
                    library_folder = session.get(
                        DocumentFolder,
                        ledger.library_folder_id,
                    )
                    if library_folder is not None:
                        mapping, _created = self._ensure_library_folder_mapping(
                            session,
                            google_subject,
                            library_folder,
                            folder_id,
                        )
                        parent_folder_id = mapping.drive_folder_id
                        session.commit()
                title = self._document_title(session, ledger, fallback_title)
                resolved_library_folder_id = ledger.library_folder_id
                resolved_location_version = ledger.library_location_version
                drive_path = (
                    f"{folder_path}/{library_path}"
                    if folder_path and library_path
                    else library_path or folder_path
                )

            with self._database.session() as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise AppError(
                        status=404,
                        code="LEDGER_DOCUMENT_NOT_FOUND",
                        title="Ledger document was not found",
                        detail="スプレッドシートに対応する台帳が見つかりません。",
                    )
                if ledger.spreadsheet_id:
                    return (
                        ledger.spreadsheet_id,
                        ledger.spreadsheet_url or self._spreadsheet_url(ledger.spreadsheet_id),
                    )
                if source_job_id is not None and ledger.current_job_id != resolved_job_id:
                    raise self._spreadsheet_output_stale()
                if (
                    ledger.library_folder_id != resolved_library_folder_id
                    or ledger.library_location_version != resolved_location_version
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_LOCATION_CHANGED",
                        title="Spreadsheet output location changed",
                        detail=(
                            "書類の保存場所が出力準備中に変更されました。"
                            "同じ操作を再試行してください。"
                        ),
                    )
                intent = session.get(SpreadsheetOutputOperation, operation_key)
                if intent is None:
                    intent = SpreadsheetOutputOperation(
                        operation_id=operation_key,
                        document_id=document_id,
                        google_subject=google_subject,
                        source_job_id=resolved_job_id,
                        source_revision=resolved_revision,
                        drive_root_folder_id=folder_id,
                        drive_parent_folder_id=parent_folder_id,
                        drive_name=title,
                        drive_folder_path=drive_path,
                        library_folder_id=resolved_library_folder_id,
                        library_location_version=resolved_location_version,
                    )
                    session.add(intent)
                elif (
                    intent.document_id != document_id
                    or intent.google_subject != google_subject
                    or intent.source_job_id != resolved_job_id
                    or intent.source_revision != resolved_revision
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_OPERATION_CONFLICT",
                        title="Spreadsheet output operation conflicts",
                        detail=(
                            "初回スプレッドシート作成の操作情報が現在の"
                            "GoogleアカウントまたはOCR結果と一致しません。"
                        ),
                    )
                elif (
                    intent.drive_root_folder_id != folder_id
                    or intent.drive_parent_folder_id != parent_folder_id
                    or intent.library_folder_id != resolved_library_folder_id
                    or intent.library_location_version != resolved_location_version
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_LOCATION_CHANGED",
                        title="Spreadsheet output location changed",
                        detail=(
                            "初回スプレッドシート作成中に保存場所が変更されました。"
                            "同期状態を確認してから再試行してください。"
                        ),
                    )
                elif intent.drive_name != title:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_NAME_CHANGED",
                        title="Spreadsheet output name changed",
                        detail=(
                            "初回スプレッドシート作成中に書類名が変更されました。"
                            "同期状態を確認してから再試行してください。"
                        ),
                    )
                intent_spreadsheet_id = intent.spreadsheet_id
                session.commit()

            if intent_spreadsheet_id:
                known = self._drive_item(intent_spreadsheet_id)
                operation_matches = [known] if known is not None else []
            else:
                operation_matches = self._find_spreadsheet_outputs(
                    operation_key,
                    document_id,
                )
            if len(operation_matches) > 1:
                raise AppError(
                    status=409,
                    code="SPREADSHEET_OUTPUT_AMBIGUOUS",
                    title="Spreadsheet output is ambiguous",
                    detail=(
                        "同じ初回作成処理に対応するスプレッドシートが複数あります。"
                        "Google Drive上の重複を整理してください。"
                    ),
                )
            if operation_matches:
                created = operation_matches[0]
            else:
                created = self._create_file(
                    name=title,
                    mime_type=_SPREADSHEET_MIME,
                    parent_id=parent_folder_id,
                    app_properties={
                        "llmOcrLedgerDocumentId": document_id,
                        "llmOcrDriveRootId": folder_id,
                        "llmOcrSpreadsheetOperationId": operation_key,
                    },
                )
            spreadsheet_id = created.get("id")
            if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
                raise AppError(
                    status=502,
                    code="GOOGLE_DRIVE_CREATE_FAILED",
                    title="Google Drive file creation failed",
                    detail="スプレッドシートの作成結果にIDがありません。",
                )
            verified_created = self._drive_item(spreadsheet_id)
            verified_properties = (
                verified_created.get("appProperties")
                if verified_created is not None
                else None
            )
            if (
                verified_created is None
                or verified_created.get("mimeType") != _SPREADSHEET_MIME
                or verified_created.get("trashed") is True
                or str(verified_created.get("name") or "") != title
                or self._string_list(verified_created.get("parents"))
                != [parent_folder_id]
                or not isinstance(verified_properties, dict)
                or verified_properties.get("llmOcrLedgerDocumentId") != document_id
                or verified_properties.get("llmOcrSpreadsheetOperationId")
                != operation_key
                or verified_properties.get("llmOcrDriveRootId") != folder_id
            ):
                raise AppError(
                    status=409,
                    code="SPREADSHEET_OUTPUT_FINAL_VERIFICATION_FAILED",
                    title="Spreadsheet output changed before completion",
                    detail=(
                        "作成済みスプレッドシートの名前、保存場所、または"
                        "出力識別情報を確認できませんでした。"
                    ),
                )
            spreadsheet_url = self._spreadsheet_url(spreadsheet_id)

            with self._database.session() as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise RuntimeError(
                        "台帳が削除されたためDriveファイルを関連付けできません。"
                    )
                if source_job_id is not None and ledger.current_job_id != resolved_job_id:
                    raise self._spreadsheet_output_stale()
                if (
                    ledger.library_folder_id != resolved_library_folder_id
                    or ledger.library_location_version != resolved_location_version
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_LOCATION_CHANGED",
                        title="Spreadsheet output location changed",
                        detail=(
                            "書類の保存場所が作成処理中に変更されました。"
                            "同じ操作を再試行してください。"
                        ),
                    )
                intent = session.get(SpreadsheetOutputOperation, operation_key)
                if (
                    intent is None
                    or intent.document_id != document_id
                    or intent.google_subject != google_subject
                    or intent.source_job_id != resolved_job_id
                    or intent.source_revision != resolved_revision
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_OPERATION_CONFLICT",
                        title="Spreadsheet output operation conflicts",
                        detail="初回スプレッドシート作成の操作情報が一致しません。",
                    )
                if ledger.spreadsheet_id and ledger.spreadsheet_id != spreadsheet_id:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_ALREADY_BOUND",
                        title="Spreadsheet output is already bound",
                        detail="台帳には別のスプレッドシートが関連付けられています。",
                    )
                ledger.spreadsheet_id = spreadsheet_id
                ledger.spreadsheet_url = spreadsheet_url
                ledger.drive_google_subject = google_subject
                ledger.drive_root_folder_id = folder_id
                ledger.drive_folder_id = parent_folder_id
                ledger.drive_folder_path = drive_path
                ledger.spreadsheet_sync_status = "CREATED"
                ledger.spreadsheet_sync_error = None
                intent.spreadsheet_id = spreadsheet_id
                session.commit()
            return spreadsheet_id, spreadsheet_url

    def create_spreadsheet_from_xlsx(
        self,
        *,
        document_id: str,
        template_id: str,
        operation_id: str,
        source_job_id: str,
        source_revision: int,
        name: str,
        content: bytes,
        expected_subject: str | None = None,
    ) -> tuple[str, str, str]:
        operation_key = operation_id.strip()[:124]
        if not operation_key:
            raise ValueError("operation_id must not be empty")
        with self._drive_operation(expected_subject) as subject:
            location_reconciliation_required = False
            intent_spreadsheet_id: str | None = None
            intent_drive_name: str | None = None
            intent_parent_baseline: str | None = None
            observed_intent = False
            legacy_output_id: str | None = None
            legacy_output_name: str | None = None
            legacy_output_parent_id: str | None = None
            with self._database.session() as session:
                template = session.get(Template, template_id)
                if template is None:
                    raise AppError(
                        status=404,
                        code="TEMPLATE_NOT_FOUND",
                        title="Template not found",
                        detail="指定されたテンプレートが見つかりません。",
                    )
                requested_title = (
                    re.sub(r"\.xlsx$", "", name, flags=re.IGNORECASE).strip()
                    or template.name
                )[:180]
                account = self._active_account(session, subject)
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise AppError(
                        status=404,
                        code="LEDGER_DOCUMENT_NOT_FOUND",
                        title="Ledger document was not found",
                        detail="スプレッドシートに対応する台帳が見つかりません。",
                    )
                if ledger.template_id != template_id:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_TEMPLATE_MISMATCH",
                        title="Spreadsheet template does not match ledger",
                        detail="台帳と異なるテンプレートの出力は関連付けできません。",
                    )
                if (
                    ledger.drive_google_subject is not None
                    and ledger.drive_google_subject != subject
                ):
                    raise self._drive_document_account_mismatch()
                if ledger.current_job_id != source_job_id:
                    raise self._spreadsheet_output_stale()
                self._assert_current_spreadsheet_revision(
                    session,
                    source_job_id,
                    source_revision,
                )
                current_confirmed = session.scalar(
                    select(ConfirmedResult).where(
                        ConfirmedResult.job_id == source_job_id
                    )
                )
                current_local_name = (
                    current_confirmed.spreadsheet_name
                    if current_confirmed is not None
                    and current_confirmed.spreadsheet_name
                    else ledger.document_name or ledger.library_name
                )
                if current_local_name and current_local_name != requested_title:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_NAME_CHANGED",
                        title="Spreadsheet output name changed",
                        detail=(
                            "書類名が出力処理中に変更されました。"
                            "同じ操作を再試行して最新の名前へ反映してください。"
                        ),
                    )
                intent = session.get(SpreadsheetOutputOperation, operation_key)
                has_persisted_output_operation = session.scalar(
                    select(SpreadsheetOutputOperation.operation_id)
                    .where(SpreadsheetOutputOperation.document_id == document_id)
                    .limit(1)
                ) is not None
                if intent is not None:
                    observed_intent = True
                    if (
                        intent.document_id != document_id
                        or intent.source_job_id != source_job_id
                        or intent.source_revision != source_revision
                    ):
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_OPERATION_CONFLICT",
                            title="Spreadsheet output operation conflicts",
                            detail=(
                                "同じ出力処理番号が別の書類またはOCR結果で使用されています。"
                                "最新結果から出力をやり直してください。"
                            ),
                        )
                    if intent.google_subject != subject:
                        raise self._drive_document_account_mismatch()
                if ledger.spreadsheet_id and ledger.drive_google_subject is None:
                    self._assert_ledger_spreadsheet_in_current_roots(
                        session,
                        ledger,
                        subject,
                    )

                google_subject = account.google_subject
                root_id = (
                    intent.drive_root_folder_id
                    if intent is not None
                    else ledger.drive_root_folder_id
                )
                parent_folder_id = (
                    intent.drive_parent_folder_id
                    if intent is not None
                    else ledger.drive_folder_id
                )
                drive_path = (
                    intent.drive_folder_path
                    if intent is not None
                    else ledger.drive_folder_path
                )
                if ledger.drive_google_subject == subject and ledger.spreadsheet_id and not root_id:
                    current_item = self._drive_item(ledger.spreadsheet_id)
                    current_parents = (
                        self._string_list(current_item.get("parents"))
                        if current_item is not None
                        else []
                    )
                    if len(current_parents) == 1:
                        parent_folder_id = current_parents[0]
                        current_mapping = session.scalar(
                            select(DocumentFolderDriveMapping).where(
                                DocumentFolderDriveMapping.google_subject == subject,
                                DocumentFolderDriveMapping.drive_folder_id
                                == parent_folder_id,
                            )
                        )
                        if current_mapping is not None:
                            root_id = current_mapping.drive_root_folder_id
                        elif parent_folder_id in {
                            candidate_id
                            for candidate_id, _candidate_name in self._library_roots(
                                session,
                                subject,
                            )
                        }:
                            root_id = parent_folder_id

                if not root_id:
                    template_folder = session.get(
                        TemplateOutputFolder,
                        (account.google_subject, template_id),
                    )
                    root_id = (
                        template_folder.folder_id
                        if template_folder is not None
                        else account.default_output_folder_id
                    )
                    root_path = (
                        template_folder.folder_path or template_folder.folder_name
                        if template_folder is not None
                        else account.default_output_folder_path
                        or account.default_output_folder_name
                    )
                else:
                    root_path = None
                if not root_id:
                    raise AppError(
                        status=409,
                        code="DRIVE_OUTPUT_FOLDER_REQUIRED",
                        title="Drive output folder is required",
                        detail="先に保存先設定でGoogle Driveフォルダーを選択してください。",
                    )

                if (
                    intent is not None
                    and ledger.library_location_version
                    != intent.library_location_version
                ):
                    location_reconciliation_required = True
                    parent_folder_id = root_id
                    library_path = self._library_folder_path(
                        session,
                        ledger.library_folder_id,
                    )
                    if ledger.library_folder_id is not None:
                        library_folder = session.get(
                            DocumentFolder,
                            ledger.library_folder_id,
                        )
                        if library_folder is not None:
                            mapping, _created = self._ensure_library_folder_mapping(
                                session,
                                google_subject,
                                library_folder,
                                root_id,
                            )
                            parent_folder_id = mapping.drive_folder_id
                    drive_path = library_path

                usable_parent = False
                if parent_folder_id:
                    parent_item = self._drive_item(parent_folder_id)
                    usable_parent = bool(
                        parent_item is not None
                        and parent_item.get("mimeType") == _FOLDER_MIME
                        and parent_item.get("trashed") is not True
                    )
                library_path = self._library_folder_path(
                    session,
                    ledger.library_folder_id,
                )
                if not usable_parent:
                    parent_folder_id = root_id
                    if ledger.library_folder_id is not None:
                        library_folder = session.get(
                            DocumentFolder,
                            ledger.library_folder_id,
                        )
                        if library_folder is not None:
                            mapping, _created = self._ensure_library_folder_mapping(
                                session,
                                google_subject,
                                library_folder,
                                root_id,
                            )
                            parent_folder_id = mapping.drive_folder_id
                    drive_path = (
                        f"{root_path}/{library_path}"
                        if root_path and library_path
                        else library_path or root_path or drive_path
                    )
                if (
                    intent is None
                    and ledger.spreadsheet_id
                    and not has_persisted_output_operation
                ):
                    legacy_item = self._drive_item(ledger.spreadsheet_id)
                    if (
                        legacy_item is None
                        or legacy_item.get("trashed") is True
                        or legacy_item.get("mimeType") != _SPREADSHEET_MIME
                    ):
                        raise AppError(
                            status=409,
                            code="DRIVE_SPREADSHEET_NOT_FOUND",
                            title="Drive spreadsheet was not found",
                            detail="既存のGoogleスプレッドシートを確認できません。",
                        )
                    legacy_parents = self._string_list(legacy_item.get("parents"))
                    normalized_legacy_name = self._drive_document_name(
                        str(legacy_item.get("name") or "")
                    )
                    if len(legacy_parents) != 1 or normalized_legacy_name is None:
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_LEGACY_CONFLICT",
                            title="Legacy spreadsheet output is ambiguous",
                            detail=(
                                "既存スプレッドシートの名前または保存場所を"
                                "一意に確認できません。"
                            ),
                        )
                    if (
                        ledger.drive_folder_id is not None
                        and legacy_parents[0] != ledger.drive_folder_id
                    ):
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_LEGACY_CONFLICT",
                            title="Legacy spreadsheet location changed",
                            detail=(
                                "既存スプレッドシートの保存場所が変更されています。"
                                "書類ライブラリを再同期してから確定してください。"
                            ),
                        )
                    legacy_output_id = ledger.spreadsheet_id
                    legacy_output_name = normalized_legacy_name
                    legacy_output_parent_id = legacy_parents[0]
                resolved_library_folder_id = ledger.library_folder_id
                resolved_location_version = ledger.library_location_version
                session.commit()

            with self._database.session() as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise AppError(
                        status=404,
                        code="LEDGER_DOCUMENT_NOT_FOUND",
                        title="Ledger document was not found",
                        detail="スプレッドシートに対応する台帳が見つかりません。",
                    )
                if ledger.current_job_id != source_job_id:
                    raise self._spreadsheet_output_stale()
                self._assert_current_spreadsheet_revision(
                    session,
                    source_job_id,
                    source_revision,
                )
                if ledger.library_location_version != resolved_location_version:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_LOCATION_CHANGED",
                        title="Spreadsheet output location changed",
                        detail=(
                            "書類の保存場所が出力準備中に変更されました。"
                            "同じ操作を再試行してください。"
                        ),
                    )
                intent = session.get(SpreadsheetOutputOperation, operation_key)
                if intent is None:
                    intent = SpreadsheetOutputOperation(
                        operation_id=operation_key,
                        document_id=document_id,
                        google_subject=google_subject,
                        source_job_id=source_job_id,
                        source_revision=source_revision,
                        drive_root_folder_id=root_id,
                        drive_parent_folder_id=(
                            legacy_output_parent_id or parent_folder_id
                        ),
                        drive_name=legacy_output_name or requested_title,
                        drive_folder_path=drive_path,
                        library_folder_id=resolved_library_folder_id,
                        library_location_version=resolved_location_version,
                        spreadsheet_id=legacy_output_id,
                    )
                    session.add(intent)
                else:
                    if not observed_intent:
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_IN_PROGRESS",
                            title="Spreadsheet output is already in progress",
                            detail=(
                                "同じ出力処理が別の要求で開始されています。"
                                "少し待ってから再試行してください。"
                            ),
                        )
                    if (
                        intent.document_id != document_id
                        or intent.source_job_id != source_job_id
                        or intent.source_revision != source_revision
                    ):
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_OPERATION_CONFLICT",
                            title="Spreadsheet output operation conflicts",
                            detail=(
                                "同じ出力処理番号が別の書類またはOCR結果で使用されています。"
                                "最新結果から出力をやり直してください。"
                            ),
                        )
                    if intent.google_subject != google_subject:
                        raise self._drive_document_account_mismatch()
                intent_spreadsheet_id = intent.spreadsheet_id
                intent_drive_name = intent.drive_name
                intent_parent_baseline = intent.drive_parent_folder_id
                location_reconciliation_required = (
                    parent_folder_id != intent.drive_parent_folder_id
                )
                session.commit()

            operation_matches: list[dict[str, Any]]
            if intent_spreadsheet_id:
                known_output = self._drive_item(intent_spreadsheet_id)
                operation_matches = [known_output] if known_output is not None else []
            else:
                operation_matches = self._find_spreadsheet_outputs(
                    operation_key,
                    document_id,
                )
            if len(operation_matches) > 1:
                raise AppError(
                    status=409,
                    code="SPREADSHEET_OUTPUT_AMBIGUOUS",
                    title="Spreadsheet output is ambiguous",
                    detail=(
                        "同じ出力処理に対応するスプレッドシートが複数あります。"
                        "Google Drive上の重複を整理してください。"
                    ),
                )
            recovered_library_folder_id: str | None = None
            recovered_location = False
            resolved_title = requested_title
            if operation_matches:
                created = operation_matches[0]
                if created.get("trashed") is True:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_TRASHED",
                        title="Spreadsheet output is in the trash",
                        detail=(
                            "作成済みスプレッドシートがGoogle Driveのゴミ箱にあります。"
                            "ファイルを復元してから再試行してください。"
                        ),
                    )
                properties = created.get("appProperties")
                if intent_spreadsheet_id and (
                    not isinstance(properties, dict)
                    or properties.get("llmOcrLedgerDocumentId") != document_id
                    or properties.get("llmOcrSpreadsheetOperationId")
                    != operation_key
                ):
                    current_properties = (
                        dict(properties) if isinstance(properties, dict) else {}
                    )
                    canonical_id = current_properties.get(
                        "llmOcrLedgerDocumentId"
                    )
                    legacy_id = current_properties.get("llmOcrDocumentId")
                    existing_operation = current_properties.get(
                        "llmOcrSpreadsheetOperationId"
                    )
                    if (
                        (canonical_id is not None and canonical_id != document_id)
                        or (legacy_id is not None and legacy_id != document_id)
                        or (
                            existing_operation is not None
                            and existing_operation != operation_key
                        )
                    ):
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_OPERATION_MISMATCH",
                            title="Spreadsheet output operation does not match",
                            detail="既存スプレッドシートは別の出力処理に属しています。",
                        )
                    current_properties["llmOcrLedgerDocumentId"] = document_id
                    current_properties["llmOcrDriveRootId"] = root_id
                    current_properties["llmOcrSpreadsheetOperationId"] = operation_key
                    created = self._request(
                        "PATCH",
                        f"{_DRIVE_FILES}/{quote(str(created['id']), safe='')}",
                        params={
                            "fields": (
                                "id,name,mimeType,parents,modifiedTime,trashed,"
                                "explicitlyTrashed,appProperties"
                            ),
                            "supportsAllDrives": "true",
                        },
                        json={"appProperties": current_properties},
                    )
                    properties = created.get("appProperties")
                if (
                    not isinstance(properties, dict)
                    or properties.get("llmOcrLedgerDocumentId") != document_id
                    or properties.get("llmOcrSpreadsheetOperationId")
                    != operation_key
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_OPERATION_MISMATCH",
                        title="Spreadsheet output operation does not match",
                        detail=(
                            "作成済みスプレッドシートの出力処理情報が一致しません。"
                            "Google Drive上のファイルを確認してください。"
                        ),
                    )
                recovered_parents = self._string_list(created.get("parents"))
                if len(recovered_parents) != 1:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_LOCATION_AMBIGUOUS",
                        title="Spreadsheet output location is ambiguous",
                        detail=(
                            "作成済みスプレッドシートの保存場所を一意に確認できません。"
                            "Google Drive上の保存場所を確認してください。"
                        ),
                    )
                actual_name = str(created.get("name") or "")
                if actual_name != requested_title:
                    if requested_title == intent_drive_name:
                        normalized_remote_name = self._drive_document_name(actual_name)
                        if normalized_remote_name is None:
                            raise AppError(
                                status=409,
                                code="SPREADSHEET_OUTPUT_NAME_CONFLICT",
                                title="Spreadsheet output name is invalid",
                                detail=(
                                    "Google Drive側の書類名を独自ライブラリで使用できません。"
                                    "名前を修正してから再同期してください。"
                                ),
                            )
                        resolved_title = normalized_remote_name
                    elif actual_name == intent_drive_name:
                        latest_created = self._drive_item(str(created["id"]))
                        if not self._drive_item_matches_snapshot(
                            latest_created,
                            created,
                        ):
                            raise AppError(
                                status=409,
                                code="SPREADSHEET_OUTPUT_NAME_CONFLICT",
                                title="Spreadsheet output changed during rename",
                                detail="名前変更直前にDrive側の書類が変更されました。",
                            )
                        created = self._request(
                            "PATCH",
                            f"{_DRIVE_FILES}/{quote(str(created['id']), safe='')}",
                            params={
                                "fields": (
                                    "id,name,mimeType,parents,modifiedTime,trashed,"
                                    "explicitlyTrashed,appProperties"
                                ),
                                "supportsAllDrives": "true",
                            },
                            json={"name": requested_title},
                        )
                        recovered_parents = self._string_list(
                            created.get("parents")
                        )
                        properties = created.get("appProperties")
                        if (
                            created.get("name") != requested_title
                            or len(recovered_parents) != 1
                            or created.get("trashed") is True
                            or not isinstance(properties, dict)
                            or properties.get("llmOcrLedgerDocumentId")
                            != document_id
                            or properties.get("llmOcrSpreadsheetOperationId")
                            != operation_key
                        ):
                            raise AppError(
                                status=409,
                                code="SPREADSHEET_OUTPUT_NAME_CONFLICT",
                                title="Spreadsheet output rename could not be verified",
                                detail="Google Drive側の名前変更結果を確認できませんでした。",
                            )
                    else:
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_NAME_CONFLICT",
                            title="Spreadsheet output name conflicts",
                            detail=(
                                "出力処理中にGoogle Drive側と独自ライブラリ側の"
                                "両方で名前が変更されました。再同期して名前を"
                                "確認してから再出力してください。"
                            ),
                        )
                if (
                    location_reconciliation_required
                    and recovered_parents != [parent_folder_id]
                ):
                    if parent_folder_id is None:
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_LOCATION_AMBIGUOUS",
                            title="Spreadsheet output target is ambiguous",
                            detail="スプレッドシートの移動先を確認できません。",
                        )
                    if recovered_parents == [intent_parent_baseline]:
                        created = self._move_drive_item(
                            str(created["id"]),
                            parent_folder_id,
                            recovered_parents,
                        )
                        recovered_parents = self._string_list(
                            created.get("parents")
                        )
                    else:
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_LOCATION_CONFLICT",
                            title="Spreadsheet output location conflicts",
                            detail=(
                                "出力処理中にGoogle Drive側と独自ライブラリ側の"
                                "両方で保存場所が変更されました。再同期して保存場所を"
                                "確認してから再出力してください。"
                            ),
                        )
                recovered_parent = recovered_parents[0]
                with self._database.session() as session:
                    ledger = session.get(LedgerDocument, document_id)
                    if ledger is None or ledger.library_deleted_at is not None:
                        raise RuntimeError(
                            "台帳が削除されたためDriveファイルを関連付けできません。"
                        )
                    current_intent = session.get(
                        SpreadsheetOutputOperation,
                        operation_key,
                    )
                    if (
                        current_intent is None
                        or current_intent.google_subject != subject
                        or current_intent.document_id != document_id
                    ):
                        raise self._drive_document_account_mismatch()
                    if location_reconciliation_required:
                        recovered_library_folder_id = resolved_library_folder_id
                        recovered_location = False
                        recovered_mapping = None
                        current_roots: dict[str, str | None] = {}
                    else:
                        recovered_mapping = session.scalar(
                            select(DocumentFolderDriveMapping).where(
                                DocumentFolderDriveMapping.google_subject == subject,
                                DocumentFolderDriveMapping.drive_folder_id
                                == recovered_parent,
                            )
                        )
                        current_roots = dict(self._library_roots(session, subject))
                    properties = created.get("appProperties")
                    tagged_root = (
                        properties.get("llmOcrDriveRootId")
                        if isinstance(properties, dict)
                        else None
                    )
                    if location_reconciliation_required:
                        recovered_root = root_id
                    elif recovered_mapping is not None:
                        recovered_root = recovered_mapping.drive_root_folder_id
                        recovered_library_folder_id = (
                            recovered_mapping.document_folder_id
                        )
                    elif (
                        recovered_parent in current_roots
                        or tagged_root == recovered_parent
                    ):
                        recovered_root = recovered_parent
                    else:
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_OUTSIDE_LIBRARY",
                            title="Spreadsheet output is outside the library",
                            detail=(
                                "作成済みスプレッドシートが同期対象外のフォルダへ"
                                "移動されています。保存場所を戻して再試行してください。"
                            ),
                        )
                    current_intent.drive_name = resolved_title
                    if not location_reconciliation_required:
                        recovered_library_path = self._library_folder_path(
                            session,
                            recovered_library_folder_id,
                        )
                        recovered_root_path = current_roots.get(recovered_root)
                        if recovered_root_path or recovered_library_path:
                            drive_path = (
                                f"{recovered_root_path}/{recovered_library_path}"
                                if recovered_root_path and recovered_library_path
                                else recovered_library_path or recovered_root_path
                            )
                        root_id = recovered_root
                        parent_folder_id = recovered_parent
                        recovered_location = True
                if not isinstance(root_id, str) or not root_id:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_LOCATION_AMBIGUOUS",
                        title="Spreadsheet output root is ambiguous",
                        detail="スプレッドシートの同期ルートを確認できません。",
                    )
                final_properties = created.get("appProperties")
                if (
                    not isinstance(final_properties, dict)
                    or final_properties.get("llmOcrLedgerDocumentId") != document_id
                    or final_properties.get("llmOcrSpreadsheetOperationId")
                    != operation_key
                    or final_properties.get("llmOcrDriveRootId") != root_id
                ):
                    merged_properties = (
                        dict(final_properties)
                        if isinstance(final_properties, dict)
                        else {}
                    )
                    merged_properties["llmOcrLedgerDocumentId"] = document_id
                    merged_properties["llmOcrSpreadsheetOperationId"] = operation_key
                    merged_properties["llmOcrDriveRootId"] = root_id
                    created = self._request(
                        "PATCH",
                        f"{_DRIVE_FILES}/{quote(str(created['id']), safe='')}",
                        params={
                            "fields": (
                                "id,name,mimeType,parents,modifiedTime,trashed,"
                                "explicitlyTrashed,appProperties"
                            ),
                            "supportsAllDrives": "true",
                        },
                        json={"appProperties": merged_properties},
                    )
                    final_properties = created.get("appProperties")
                    if (
                        not isinstance(final_properties, dict)
                        or final_properties.get("llmOcrLedgerDocumentId")
                        != document_id
                        or final_properties.get("llmOcrSpreadsheetOperationId")
                        != operation_key
                        or final_properties.get("llmOcrDriveRootId") != root_id
                    ):
                        raise AppError(
                            status=409,
                            code="SPREADSHEET_OUTPUT_OPERATION_MISMATCH",
                            title="Spreadsheet output metadata could not be verified",
                            detail="Google Drive側の出力識別情報を確認できませんでした。",
                        )
            else:
                boundary = f"llm_ocr_{uuid4().hex}"
                metadata = json.dumps(
                    {
                        "name": requested_title,
                        "mimeType": _SPREADSHEET_MIME,
                        "parents": [parent_folder_id],
                        "appProperties": {
                            "llmOcrLedgerDocumentId": document_id,
                            "llmOcrDriveRootId": root_id,
                            "llmOcrSpreadsheetOperationId": operation_key,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                body = b"".join(
                    (
                        f"--{boundary}\r\n".encode("ascii"),
                        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                        metadata,
                        f"\r\n--{boundary}\r\n".encode("ascii"),
                        f"Content-Type: {_XLSX_MIME}\r\n\r\n".encode("ascii"),
                        content,
                        f"\r\n--{boundary}--\r\n".encode("ascii"),
                    )
                )
                created = self._request(
                    "POST",
                    _DRIVE_UPLOAD_FILES,
                    params={
                        "uploadType": "multipart",
                        "fields": "id,name,mimeType,parents,appProperties",
                        "supportsAllDrives": "true",
                    },
                    headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                    data=body,
                )
            spreadsheet_id = created.get("id")
            if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
                raise AppError(
                    status=502,
                    code="GOOGLE_DRIVE_CONVERSION_FAILED",
                    title="Google Drive conversion failed",
                    detail="入力済みXLSXをGoogleスプレッドシートへ変換できませんでした。",
                )
            spreadsheet_url = self._spreadsheet_url(spreadsheet_id)
            if root_id is None or parent_folder_id is None:
                raise AppError(
                    status=409,
                    code="SPREADSHEET_OUTPUT_LOCATION_AMBIGUOUS",
                    title="Spreadsheet output location is ambiguous",
                    detail="スプレッドシートの保存先を確認できません。",
                )
            verified_created = self._drive_item(spreadsheet_id)
            verified_properties = (
                verified_created.get("appProperties")
                if verified_created is not None
                else None
            )
            if (
                verified_created is None
                or verified_created.get("mimeType") != _SPREADSHEET_MIME
                or verified_created.get("trashed") is True
                or self._drive_document_name(
                    str(verified_created.get("name") or "")
                )
                != resolved_title
                or self._string_list(verified_created.get("parents"))
                != [parent_folder_id]
                or not isinstance(verified_properties, dict)
                or verified_properties.get("llmOcrLedgerDocumentId") != document_id
                or verified_properties.get("llmOcrSpreadsheetOperationId")
                != operation_key
                or verified_properties.get("llmOcrDriveRootId") != root_id
            ):
                raise AppError(
                    status=409,
                    code="SPREADSHEET_OUTPUT_FINAL_VERIFICATION_FAILED",
                    title="Spreadsheet output changed before completion",
                    detail=(
                        "Google Drive側の名前、保存場所、または出力識別情報が"
                        "処理中に変更されました。再同期してから再試行してください。"
                    ),
                )
            created = verified_created
            with self._database.session() as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise RuntimeError(
                        "台帳が削除されたためDriveファイルを関連付けできません。"
                    )
                if ledger.current_job_id != source_job_id:
                    raise self._spreadsheet_output_stale()
                self._assert_current_spreadsheet_revision(
                    session,
                    source_job_id,
                    source_revision,
                )
                current_confirmed = session.scalar(
                    select(ConfirmedResult).where(
                        ConfirmedResult.job_id == source_job_id
                    )
                )
                current_local_name = (
                    current_confirmed.spreadsheet_name
                    if current_confirmed is not None
                    and current_confirmed.spreadsheet_name
                    else ledger.document_name or ledger.library_name
                )
                if current_local_name and current_local_name != requested_title:
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_NAME_CHANGED",
                        title="Spreadsheet output name changed",
                        detail=(
                            "書類名が出力処理中に変更されました。"
                            "同じ操作を再試行して最新の名前へ反映してください。"
                        ),
                    )
                intent = session.get(SpreadsheetOutputOperation, operation_key)
                if (
                    intent is None
                    or intent.document_id != document_id
                    or intent.google_subject != google_subject
                    or intent.source_job_id != source_job_id
                    or intent.source_revision != source_revision
                ):
                    raise self._drive_document_account_mismatch()
                if (
                    ledger.library_location_version
                    != resolved_location_version
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_OUTPUT_LOCATION_CHANGED",
                        title="Spreadsheet output location changed",
                        detail=(
                            "書類の保存場所が出力処理中に変更されました。"
                            "同じ操作を再試行して最新の保存場所へ反映してください。"
                        ),
                    )
                if (
                    ledger.drive_google_subject is not None
                    and ledger.drive_google_subject != google_subject
                ):
                    raise self._drive_document_account_mismatch()
                ledger.spreadsheet_id = spreadsheet_id
                ledger.spreadsheet_url = spreadsheet_url
                ledger.drive_google_subject = google_subject
                ledger.drive_root_folder_id = root_id
                ledger.drive_folder_id = parent_folder_id
                ledger.drive_folder_path = drive_path
                if (
                    recovered_location
                    and ledger.library_folder_id != recovered_library_folder_id
                ):
                    ledger.library_folder_id = recovered_library_folder_id
                    ledger.library_location_version += 1
                    intent.library_folder_id = recovered_library_folder_id
                    intent.library_location_version = ledger.library_location_version
                    intent.drive_root_folder_id = root_id
                    intent.drive_parent_folder_id = parent_folder_id
                    intent.drive_folder_path = drive_path
                intent.spreadsheet_id = spreadsheet_id
                intent.drive_name = resolved_title
                intent.drive_root_folder_id = root_id
                intent.drive_parent_folder_id = parent_folder_id
                intent.drive_folder_path = drive_path
                intent.library_folder_id = ledger.library_folder_id
                intent.library_location_version = ledger.library_location_version
                ledger.document_name = resolved_title
                ledger.library_name = resolved_title
                ledger.spreadsheet_sync_status = "CREATED"
                ledger.spreadsheet_sync_error = None
                session.commit()
            return spreadsheet_id, spreadsheet_url, resolved_title

    @staticmethod
    def _assert_current_spreadsheet_revision(
        session: Session,
        job_id: str,
        revision: int,
    ) -> None:
        current = session.scalar(
            select(ConfirmedResult).where(ConfirmedResult.job_id == job_id)
        )
        if current is None or current.ocr_result_revision != revision:
            raise GoogleDriveService._spreadsheet_output_stale()

    @staticmethod
    def _spreadsheet_output_stale() -> AppError:
        return AppError(
            status=409,
            code="SPREADSHEET_OUTPUT_STALE",
            title="Spreadsheet output is stale",
            detail=(
                "OCR結果が更新されたため、古い内容のスプレッドシートは"
                "関連付けませんでした。最新結果を再出力してください。"
            ),
        )

    def export_spreadsheet_pdf(
        self,
        spreadsheet_id: str,
        *,
        paper_size: str = "A4",
        orientation: str = "portrait",
        expected_subject: str | None = None,
    ) -> bytes:
        import os
        import shutil
        import subprocess
        import tempfile
        from io import BytesIO
        from pathlib import Path

        from openpyxl import load_workbook  # type: ignore[import-untyped]
        from openpyxl.worksheet.properties import (
            PageSetupProperties,  # type: ignore[import-untyped]
        )

        with self._drive_operation(expected_subject) as subject:
            self._assert_spreadsheet_in_current_roots(spreadsheet_id)
            response = self._authorized_session(subject).request(  # type: ignore[no-untyped-call]
                "GET",
                f"{_DRIVE_FILES}/{quote(spreadsheet_id, safe='')}/export",
                params={"mimeType": _XLSX_MIME},
                timeout=30,
            )
        if response.status_code >= 400:
            raise AppError(
                status=502,
                code="GOOGLE_DRIVE_EXPORT_ERROR",
                title="Google Drive export failed",
                detail=(
                    "構造化済みスプレッドシートを印刷用PDFへ変換できませんでした。"
                    "Google連携を確認してください。"
                ),
            )
        configured = os.getenv("LLM_OCR_LIBREOFFICE_PATH") or os.getenv(
            "LIBREOFFICE_PATH"
        )
        candidates = [
            configured,
            shutil.which("soffice"),
            shutil.which("soffice.exe"),
            str(
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "LibreOffice/program/soffice.exe"
            ),
            str(
                Path(
                    os.environ.get(
                        "PROGRAMFILES(X86)",
                        "C:/Program Files (x86)",
                    )
                )
                / "LibreOffice/program/soffice.exe"
            ),
        ]
        soffice = next(
            (
                Path(candidate)
                for candidate in candidates
                if candidate and Path(candidate).is_file()
            ),
            None,
        )
        if soffice is None:
            raise AppError(
                status=503,
                code="SPREADSHEET_PRINT_RENDERER_NOT_AVAILABLE",
                title="Spreadsheet print renderer is not available",
                detail="構造化済みシートの1ページ印刷に必要なLibreOfficeがインストールされていません。",
            )

        paper_sizes = {
            "LETTER": "1",
            "A4": "9",
            "A5": "11",
            "B5": "13",
        }
        normalized_paper_size = paper_sizes.get(paper_size, paper_sizes["A4"])
        normalized_orientation = (
            "landscape" if orientation == "landscape" else "portrait"
        )

        with tempfile.TemporaryDirectory(
            prefix="llm-ocr-spreadsheet-print-"
        ) as directory_name:
            directory = Path(directory_name)
            source_path = directory / "spreadsheet.xlsx"
            profile_path = directory / "libreoffice-profile"
            profile_path.mkdir()
            try:
                workbook = load_workbook(BytesIO(bytes(response.content)))
                for worksheet in workbook.worksheets:
                    if worksheet.sheet_state != "visible":
                        continue
                    worksheet.print_area = worksheet.calculate_dimension()
                    if worksheet.sheet_properties.pageSetUpPr is None:
                        worksheet.sheet_properties.pageSetUpPr = PageSetupProperties(
                            fitToPage=True,
                            autoPageBreaks=False,
                        )
                    else:
                        worksheet.sheet_properties.pageSetUpPr.fitToPage = True
                        worksheet.sheet_properties.pageSetUpPr.autoPageBreaks = False
                    worksheet.page_setup.paperSize = normalized_paper_size
                    worksheet.page_setup.orientation = normalized_orientation
                    worksheet.page_setup.fitToWidth = 1
                    worksheet.page_setup.fitToHeight = 1
                    worksheet.page_setup.scale = None
                    worksheet.page_margins.left = 0
                    worksheet.page_margins.right = 0
                    worksheet.page_margins.top = 0
                    worksheet.page_margins.bottom = 0
                    worksheet.page_margins.header = 0
                    worksheet.page_margins.footer = 0
                workbook.save(source_path)
                workbook.close()
            except Exception as exc:
                raise AppError(
                    status=422,
                    code="SPREADSHEET_PRINT_PREPARATION_FAILED",
                    title="Spreadsheet print preparation failed",
                    detail="構造化済みシートを1ページ印刷用に準備できませんでした。",
                ) from exc

            command = [
                str(soffice),
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_path.as_uri()}",
                "--convert-to",
                'pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}',
                "--outdir",
                str(directory),
                str(source_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=directory,
                    capture_output=True,
                    check=False,
                    timeout=90,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise AppError(
                    status=504,
                    code="SPREADSHEET_PRINT_RENDER_TIMEOUT",
                    title="Spreadsheet print rendering timed out",
                    detail="構造化済みシートの1ページ変換が90秒以内に完了しませんでした。",
                ) from exc

            pdf_path = directory / "spreadsheet.pdf"
            if completed.returncode != 0 or not pdf_path.is_file():
                raise AppError(
                    status=422,
                    code="SPREADSHEET_PRINT_RENDER_FAILED",
                    title="Spreadsheet print rendering failed",
                    detail="構造化済みシートを1ページのPDFへ変換できませんでした。",
                )
            return pdf_path.read_bytes()

    def sync_document_library(
        self,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        result: dict[str, Any] = {
            "status": "SYNCED",
            "lastAttemptAt": now,
            "lastSuccessAt": None,
            "foldersImported": 0,
            "foldersMirrored": 0,
            "foldersUpdated": 0,
            "documentsUpdated": 0,
            "conflicts": 0,
            "conflictItems": [],
            "error": None,
            "_driveChildrenCache": {},
            "_driveItemCache": {},
        }
        try:
            subject = self._require_subject(expected_subject)
        except AppError as exc:
            result["status"] = "REAUTHORIZATION_REQUIRED"
            result["error"] = exc.detail
            return result

        with self._database.session() as session:
            previous_state = session.get(DocumentLibrarySyncState, subject)
            if previous_state is not None:
                result["lastSuccessAt"] = previous_state.last_success_at
                previous_change_token = previous_state.drive_change_token
                previous_roots = list(previous_state.drive_roots_json or [])
                previous_success_at = previous_state.last_success_at
            else:
                previous_change_token = None
                previous_roots = []
                previous_success_at = None

        with self._drive_operation(subject):
            self._write_library_sync_state(subject, result, status="SYNCING")
            try:
                with self._database.session() as session:
                    roots = self._library_roots(session, subject)
                if not roots:
                    raise AppError(
                        status=409,
                        code="DRIVE_OUTPUT_FOLDER_REQUIRED",
                        title="Drive output folder is required",
                        detail="保存先設定でGoogle Driveの保存先を選択してください。",
                    )
                result["_rootNames"] = {
                    root_id: root_name or root_id for root_id, root_name in roots
                }

                root_ids = {root_id for root_id, _root_name in roots}
                current_roots = sorted(root_ids)
                skip_full_sync = False
                if (
                    previous_change_token
                    and previous_roots == current_roots
                    and not self._has_local_library_drift(
                        subject,
                        roots,
                        previous_success_at,
                    )
                ):
                    try:
                        changes, next_change_token = self._list_drive_changes(
                            previous_change_token
                        )
                    except AppError as exc:
                        if exc.code != "DRIVE_CHANGE_TOKEN_INVALID":
                            raise
                    else:
                        if not self._changes_affect_library(
                            subject,
                            root_ids,
                            changes,
                        ):
                            result["status"] = "SYNCED"
                            result["lastSuccessAt"] = datetime.now(UTC)
                            result["_driveChangeToken"] = next_change_token
                            result["_driveRoots"] = current_roots
                            self._write_library_sync_state(
                                subject,
                                result,
                                status="SYNCED",
                            )
                            skip_full_sync = True

                if not skip_full_sync:
                    for root_id, _root_name in roots:
                        self._import_drive_folder_tree(
                            subject,
                            root_id,
                            result,
                            excluded_folder_ids=root_ids - {root_id},
                        )

                    with self._database.session() as session:
                        folders = self._folders_parent_first(list(
                            session.scalars(
                                select(DocumentFolder).order_by(
                                    DocumentFolder.created_at,
                                    DocumentFolder.id,
                                )
                            )
                        ))
                        for root_id, _root_name in roots:
                            verified_folder_ids: set[str] = set()
                            for folder in folders:
                                if (
                                    folder.parent_id is not None
                                    and folder.parent_id not in verified_folder_ids
                                ):
                                    self._record_library_conflict(
                                        result,
                                        code="DRIVE_FOLDER_ANCESTOR_CONFLICT",
                                        resource_id=folder.id,
                                        resource_name=folder.name,
                                        detail=(
                                            "親フォルダに未解決の競合があるため、"
                                            "配下のフォルダは変更していません。"
                                        ),
                                        drive_root_id=root_id,
                                    )
                                    continue
                                _mapping, created = self._ensure_library_folder_mapping(
                                    session,
                                    subject,
                                    folder,
                                    root_id,
                                    result=result,
                                    verified_folder_ids=verified_folder_ids,
                                )
                                if created:
                                    result["foldersMirrored"] += 1
                        session.commit()

                    self._sync_drive_documents(subject, roots, result)
                    result["status"] = (
                        "PARTIAL" if result["conflicts"] else "SYNCED"
                    )
                    result["lastSuccessAt"] = datetime.now(UTC)
                    try:
                        result["_driveChangeToken"] = (
                            self._start_drive_change_token()
                        )
                        result["_driveRoots"] = current_roots
                    except AppError:
                        result["_driveChangeToken"] = None
                        result["_driveRoots"] = current_roots
                    self._write_library_sync_state(
                        subject,
                        result,
                        status=result["status"],
                    )
            except AppError as exc:
                result["status"] = (
                    "REAUTHORIZATION_REQUIRED"
                    if exc.code
                    in {
                        "GOOGLE_ACCOUNT_REQUIRED",
                        "GOOGLE_AUTH_REFRESH_FAILED",
                        "GOOGLE_AUTH_REAUTHORIZATION_REQUIRED",
                    }
                    else "ERROR"
                )
                result["error"] = exc.detail
                self._write_library_sync_state(subject, result, status=result["status"])
            except Exception:
                result["status"] = "ERROR"
                result["error"] = "Google Driveとの同期中に予期しないエラーが発生しました。"
                self._write_library_sync_state(subject, result, status="ERROR")
        result.pop("_skipMappingIds", None)
        result.pop("_recreatedFolderParents", None)
        result.pop("_recreatedFolderTargets", None)
        result.pop("_rootNames", None)
        result.pop("_driveChildrenCache", None)
        result.pop("_driveItemCache", None)
        result.pop("_driveChangeToken", None)
        result.pop("_driveRoots", None)
        return result

    def push_document_library_to_drive(
        self,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        """Reflect local library placement without importing Drive-side changes."""
        now = datetime.now(UTC)
        result: dict[str, Any] = {
            "mode": "LIBRARY_TO_DRIVE",
            "status": "SYNCED",
            "lastAttemptAt": now,
            "lastSuccessAt": None,
            "foldersImported": 0,
            "foldersMirrored": 0,
            "foldersUpdated": 0,
            "documentsUpdated": 0,
            "conflicts": 0,
            "conflictItems": [],
            "error": None,
            "_driveChildrenCache": {},
            "_driveItemCache": {},
        }
        try:
            subject = self._require_subject(expected_subject)
        except AppError as exc:
            result["status"] = "REAUTHORIZATION_REQUIRED"
            result["error"] = exc.detail
            return result

        try:
            with self._drive_operation(subject):
                with self._database.session() as session:
                    roots = self._library_roots(session, subject)
                    folders = self._folders_parent_first(list(
                        session.scalars(
                            select(DocumentFolder).order_by(
                                DocumentFolder.created_at,
                                DocumentFolder.id,
                            )
                        )
                    ))
                if not roots:
                    raise AppError(
                        status=409,
                        code="DRIVE_OUTPUT_FOLDER_REQUIRED",
                        title="Drive output folder is required",
                        detail="保存先設定でGoogle Driveの保存先を選択してください。",
                    )
                result["_rootNames"] = {
                    root_id: root_name or root_id for root_id, root_name in roots
                }

                for root_id, _root_name in roots:
                    verified_folder_ids: set[str] = set()
                    for folder_snapshot in folders:
                        if (
                            folder_snapshot.parent_id is not None
                            and folder_snapshot.parent_id not in verified_folder_ids
                        ):
                            self._record_library_conflict(
                                result,
                                code="DRIVE_FOLDER_ANCESTOR_CONFLICT",
                                resource_id=folder_snapshot.id,
                                resource_name=folder_snapshot.name,
                                detail=(
                                    "親フォルダに未解決の競合があるため、"
                                    "配下のフォルダは変更していません。"
                                ),
                                drive_root_id=root_id,
                            )
                            continue
                        with self._database.session() as session:
                            folder = session.get(DocumentFolder, folder_snapshot.id)
                            if folder is None:
                                self._record_library_conflict(
                                    result,
                                    code="DOCUMENT_FOLDER_NOT_FOUND",
                                    resource_id=folder_snapshot.id,
                                    resource_name=folder_snapshot.name,
                                    detail=(
                                        "同期処理中に独自ライブラリのフォルダが"
                                        "削除されたため変更していません。"
                                    ),
                                    drive_root_id=root_id,
                                )
                                continue
                            mapping_before = session.scalar(
                                select(DocumentFolderDriveMapping).where(
                                    DocumentFolderDriveMapping.google_subject == subject,
                                    DocumentFolderDriveMapping.document_folder_id
                                    == folder.id,
                                    DocumentFolderDriveMapping.drive_root_folder_id
                                    == root_id,
                                )
                            )
                            before_snapshot = (
                                (
                                    mapping_before.drive_folder_id,
                                    mapping_before.drive_name,
                                    mapping_before.drive_parent_folder_id,
                                )
                                if mapping_before is not None
                                else None
                            )
                            conflicts_before = result["conflicts"]
                            try:
                                mapping, created = self._ensure_library_folder_mapping(
                                    session,
                                    subject,
                                    folder,
                                    root_id,
                                    result=result,
                                    verified_folder_ids=verified_folder_ids,
                                )
                            except AppError as exc:
                                if exc.status >= 500 or exc.code in {
                                    "GOOGLE_ACCOUNT_REQUIRED",
                                    "GOOGLE_AUTH_REFRESH_FAILED",
                                    "GOOGLE_AUTH_REAUTHORIZATION_REQUIRED",
                                    "DRIVE_FOLDER_CREATE_PARTIAL",
                                }:
                                    raise
                                self._record_library_conflict(
                                    result,
                                    code=exc.code,
                                    resource_id=folder.id,
                                    resource_name=folder.name,
                                    detail=exc.detail,
                                    drive_root_id=root_id,
                                )
                                continue
                            session.commit()
                            after_snapshot = (
                                mapping.drive_folder_id,
                                mapping.drive_name,
                                mapping.drive_parent_folder_id,
                            )
                            if created:
                                result["foldersMirrored"] += 1
                            elif (
                                result["conflicts"] == conflicts_before
                                and before_snapshot != after_snapshot
                            ):
                                result["foldersUpdated"] += 1

            # move_library_document serializes each Drive mutation itself, so it
            # must run after the folder phase releases the non-reentrant lock.
            with self._database.session() as session:
                document_snapshots = [
                    {
                        "id": ledger.id,
                        "name": (
                            ledger.document_name
                            or ledger.library_name
                            or ledger.individual_id
                        ),
                        "folderId": ledger.library_folder_id,
                        "subject": ledger.drive_google_subject,
                        "rootId": (
                            ledger.drive_root_folder_id
                            or self._effective_root_for_document(
                                session,
                                subject,
                                ledger,
                            )
                        ),
                        "driveFolderId": ledger.drive_folder_id,
                        "driveFolderPath": ledger.drive_folder_path,
                    }
                    for ledger in session.scalars(
                        select(LedgerDocument).where(
                            LedgerDocument.spreadsheet_id.is_not(None),
                            LedgerDocument.library_deleted_at.is_(None),
                        )
                    )
                ]

            for document in document_snapshots:
                document_id = str(document["id"])
                document_name = str(document["name"])
                root_id_value = document["rootId"]
                root_id = str(root_id_value) if root_id_value else None
                owner = document["subject"]
                if owner is None:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_DOCUMENT_OWNER_UNCONFIRMED",
                        resource_id=document_id,
                        resource_name=document_name,
                        detail=(
                            "所有アカウントが確定していない旧書類は、"
                            "誤ったアカウントへ移管しないため変更していません。"
                        ),
                        drive_root_id=root_id,
                    )
                    continue
                if owner != subject:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_DOCUMENT_ACCOUNT_MISMATCH",
                        resource_id=document_id,
                        resource_name=document_name,
                        detail=(
                            "この書類は別のGoogleアカウントに属するため"
                            "変更していません。"
                        ),
                        drive_root_id=root_id,
                    )
                    continue
                if not root_id:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_ROOT_UNKNOWN",
                        resource_id=document_id,
                        resource_name=document_name,
                        detail="スプレッドシートの同期ルートを確認できません。",
                    )
                    continue

                folder_id_value = document["folderId"]
                folder_id = str(folder_id_value) if folder_id_value else None
                with self._database.session() as session:
                    desired_parent = root_id
                    if folder_id is not None:
                        mapping = session.scalar(
                            select(DocumentFolderDriveMapping).where(
                                DocumentFolderDriveMapping.google_subject == subject,
                                DocumentFolderDriveMapping.document_folder_id
                                == folder_id,
                                DocumentFolderDriveMapping.drive_root_folder_id
                                == root_id,
                            )
                        )
                        if mapping is None:
                            self._record_library_conflict(
                                result,
                                code="DRIVE_FOLDER_ANCESTOR_CONFLICT",
                                resource_id=document_id,
                                resource_name=document_name,
                                detail=(
                                    "保存先フォルダのDrive対応を確認できないため、"
                                    "書類を変更していません。"
                                ),
                                drive_root_id=root_id,
                            )
                            continue
                        desired_parent = mapping.drive_folder_id
                    desired_path = self._library_folder_path(session, folder_id)
                needs_update = (
                    document["driveFolderId"] != desired_parent
                    or document["driveFolderPath"] != desired_path
                )
                try:
                    self.move_library_document(
                        document_id,
                        folder_id,
                        expected_version=None,
                        expected_subject=subject,
                    )
                except AppError as exc:
                    if exc.status >= 500 or exc.code in {
                        "GOOGLE_ACCOUNT_REQUIRED",
                        "GOOGLE_AUTH_REFRESH_FAILED",
                        "GOOGLE_AUTH_REAUTHORIZATION_REQUIRED",
                        "DRIVE_DOCUMENT_MOVE_PARTIAL",
                    }:
                        raise
                    self._record_library_conflict(
                        result,
                        code=exc.code,
                        resource_id=document_id,
                        resource_name=document_name,
                        detail=exc.detail,
                        drive_root_id=root_id,
                    )
                    continue
                if needs_update:
                    result["documentsUpdated"] += 1

            result["status"] = "PARTIAL" if result["conflicts"] else "SYNCED"
            result["lastSuccessAt"] = datetime.now(UTC)
        except AppError as exc:
            result["status"] = (
                "REAUTHORIZATION_REQUIRED"
                if exc.code
                in {
                    "GOOGLE_ACCOUNT_REQUIRED",
                    "GOOGLE_AUTH_REFRESH_FAILED",
                    "GOOGLE_AUTH_REAUTHORIZATION_REQUIRED",
                }
                else "ERROR"
            )
            result["error"] = exc.detail
        except Exception:
            result["status"] = "ERROR"
            result["error"] = (
                "Google Driveへの反映中に予期しないエラーが発生しました。"
            )

        result.pop("_skipMappingIds", None)
        result.pop("_recreatedFolderParents", None)
        result.pop("_recreatedFolderTargets", None)
        result.pop("_rootNames", None)
        result.pop("_driveChildrenCache", None)
        result.pop("_driveItemCache", None)
        return result

    def mirror_library_folder(
        self,
        folder_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        subject = self._require_subject(expected_subject)
        with self._drive_operation(subject), self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                raise AppError(
                    status=404,
                    code="DOCUMENT_FOLDER_NOT_FOUND",
                    title="Document folder was not found",
                    detail="フォルダが見つかりません。",
                )
            roots = self._library_roots(session, subject)
            mirrored = 0
            for root_id, _root_name in roots:
                _mapping, created = self._ensure_library_folder_mapping(
                    session,
                    subject,
                    folder,
                    root_id,
                )
                if created:
                    mirrored += 1
            session.commit()
            return {"mirrored": mirrored, "rootCount": len(roots)}

    def rename_mirrored_library_folder(
        self,
        folder_id: str,
        name: str,
        expected_subject: str | None = None,
    ) -> None:
        subject = self._require_subject(expected_subject)
        with self._drive_operation(subject), self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                raise AppError(
                    status=404,
                    code="DOCUMENT_FOLDER_NOT_FOUND",
                    title="Document folder was not found",
                    detail="フォルダが見つかりません。",
                )
            parent_condition = (
                DocumentFolder.parent_id.is_(None)
                if folder.parent_id is None
                else DocumentFolder.parent_id == folder.parent_id
            )
            duplicate = session.scalar(
                select(DocumentFolder.id)
                .where(
                    parent_condition,
                    DocumentFolder.normalized_name == name.casefold(),
                    DocumentFolder.id != folder_id,
                )
                .limit(1)
            )
            if duplicate is not None:
                raise AppError(
                    status=409,
                    code="DOCUMENT_FOLDER_NAME_CONFLICT",
                    title="Document folder name already exists",
                    detail="同じ場所に同名のフォルダがあります。",
                )
            folder.name = name
            folder.normalized_name = name.casefold()
            # Reserve the unique sibling name before mutating Drive. The
            # transaction is rolled back if any Drive update fails.
            session.flush()
            mappings = list(
                session.scalars(
                    select(DocumentFolderDriveMapping).where(
                        DocumentFolderDriveMapping.google_subject == subject,
                        DocumentFolderDriveMapping.document_folder_id == folder_id,
                        DocumentFolderDriveMapping.drive_root_folder_id.in_(
                            {
                                root_id
                                for root_id, _root_name in self._library_roots(
                                    session,
                                    subject,
                                )
                            }
                        ),
                    ).order_by(DocumentFolderDriveMapping.drive_root_folder_id)
                )
            )
            current_items: list[
                tuple[DocumentFolderDriveMapping, dict[str, Any], str]
            ] = []
            for mapping in mappings:
                current = self._drive_item(mapping.drive_folder_id)
                parents = self._string_list(current.get("parents")) if current else []
                if (
                    current is None
                    or current.get("trashed") is True
                    or str(current.get("name") or "") != mapping.drive_name
                    or parents != [mapping.drive_parent_folder_id]
                ):
                    raise AppError(
                        status=409,
                        code="DOCUMENT_FOLDER_SYNC_CONFLICT",
                        title="Document folder sync conflict",
                        detail=(
                            "Google Drive側にも未同期の変更があります。"
                            "再同期してから名前を変更してください。"
                        ),
                    )
                current_items.append(
                    (mapping, current, str(current.get("name") or folder.name))
                )

            attempted_items: list[tuple[str, str]] = []
            try:
                for mapping, _current, previous_name in current_items:
                    attempted_items.append((mapping.drive_folder_id, previous_name))
                    item = self._request(
                        "PATCH",
                        f"{_DRIVE_FILES}/{quote(mapping.drive_folder_id, safe='')}",
                        params={
                            "fields": (
                                "id,name,parents,modifiedTime,trashed,"
                                "explicitlyTrashed,appProperties"
                            ),
                            "supportsAllDrives": "true",
                        },
                        json={"name": name},
                    )
                    final_item = self._drive_item(mapping.drive_folder_id)
                    if not self._drive_item_matches_snapshot(final_item, item):
                        raise AppError(
                            status=409,
                            code="DRIVE_FOLDER_RENAME_CONFLICT",
                            title="Drive folder changed during rename",
                            detail=(
                                "名前変更直後にGoogle Drive側のフォルダが変更されました。"
                                "元の名前へ戻せる範囲だけ補償します。"
                            ),
                        )
                    assert final_item is not None
                    self._update_mapping_snapshot(mapping, final_item)
                session.commit()
            except Exception as exc:
                compensation_failed = False
                for drive_folder_id, previous_name in reversed(attempted_items):
                    try:
                        current = self._drive_item(drive_folder_id)
                        if current is not None and current.get("name") == name:
                            self._request(
                                "PATCH",
                                f"{_DRIVE_FILES}/{quote(drive_folder_id, safe='')}",
                                params={
                                    "fields": (
                                        "id,name,parents,modifiedTime,trashed,"
                                        "explicitlyTrashed,appProperties"
                                    ),
                                    "supportsAllDrives": "true",
                                },
                                json={"name": previous_name},
                            )
                    except Exception:
                        compensation_failed = True
                if compensation_failed:
                    raise AppError(
                        status=409,
                        code="DRIVE_FOLDER_RENAME_PARTIAL",
                        title="Drive folder rename was partially applied",
                        detail=(
                            "一部のGoogle Driveフォルダだけ名前が変更されました。"
                            "再同期して競合の詳細を確認してください。"
                        ),
                    ) from exc
                raise

    def delete_mirrored_library_folder(
        self,
        folder_id: str,
        expected_subject: str | None = None,
    ) -> None:
        subject = self._require_subject(expected_subject)
        with self._drive_operation(subject), self._database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            if folder is None:
                return
            has_children = session.scalar(
                select(DocumentFolder.id)
                .where(DocumentFolder.parent_id == folder_id)
                .limit(1)
            )
            has_documents = session.scalar(
                select(LedgerDocument.id)
                .where(LedgerDocument.library_folder_id == folder_id)
                .limit(1)
            )
            if has_children is not None or has_documents is not None:
                raise AppError(
                    status=409,
                    code="DOCUMENT_FOLDER_NOT_EMPTY",
                    title="Document folder is not empty",
                    detail="書類または子フォルダを移動してから削除してください。",
                )
            mappings = list(
                session.scalars(
                    select(DocumentFolderDriveMapping).where(
                        DocumentFolderDriveMapping.google_subject == subject,
                        DocumentFolderDriveMapping.document_folder_id == folder_id,
                        DocumentFolderDriveMapping.drive_root_folder_id.in_(
                            {
                                root_id
                                for root_id, _root_name in self._library_roots(
                                    session,
                                    subject,
                                )
                            }
                        ),
                    )
                )
            )
            current_items: list[
                tuple[DocumentFolderDriveMapping, dict[str, Any], bool, set[str]]
            ] = []
            for mapping in mappings:
                current = self._drive_item(mapping.drive_folder_id)
                parents = self._string_list(current.get("parents")) if current else []
                properties = current.get("appProperties") if current else None
                managed_folder_id = (
                    properties.get("llmOcrLibraryFolderId")
                    if isinstance(properties, dict)
                    else None
                )
                if (
                    current is None
                    or str(current.get("name") or "") != mapping.drive_name
                    or parents != [mapping.drive_parent_folder_id]
                    or managed_folder_id != folder_id
                ):
                    raise AppError(
                        status=409,
                        code="DOCUMENT_FOLDER_SYNC_CONFLICT",
                        title="Document folder sync conflict",
                        detail=(
                            "Google Drive側にも未同期の変更があります。"
                            "再同期してからフォルダを削除してください。"
                        ),
                    )
                if self._list_drive_children(mapping.drive_folder_id):
                    raise AppError(
                        status=409,
                        code="DRIVE_FOLDER_NOT_EMPTY",
                        title="Drive folder is not empty",
                        detail=(
                            "Google Drive側の同名フォルダにファイルがあります。"
                            "Drive側を空にしてから削除してください。"
                        ),
                    )
                child_snapshot = {
                    str(child.get("id"))
                    for child in self._list_drive_children(
                        mapping.drive_folder_id,
                        include_trashed=True,
                    )
                    if child.get("id")
                }
                current_items.append(
                    (
                        mapping,
                        current,
                        current.get("trashed") is True,
                        child_snapshot,
                    )
                )
            attempted_folder_ids: list[str] = []
            try:
                for mapping, _current, already_trashed, child_snapshot in current_items:
                    if already_trashed:
                        continue
                    latest = self._drive_item(mapping.drive_folder_id)
                    if (
                        latest is None
                        or latest.get("trashed") is True
                        or str(latest.get("name") or "") != mapping.drive_name
                        or self._string_list(latest.get("parents"))
                        != [mapping.drive_parent_folder_id]
                        or self._list_drive_children(mapping.drive_folder_id)
                        or {
                            str(child.get("id"))
                            for child in self._list_drive_children(
                                mapping.drive_folder_id,
                                include_trashed=True,
                            )
                            if child.get("id")
                        }
                        != child_snapshot
                    ):
                        raise AppError(
                            status=409,
                            code="DRIVE_FOLDER_NOT_EMPTY",
                            title="Drive folder changed before deletion",
                            detail=(
                                "削除直前にGoogle Drive側のフォルダが変更されました。"
                                "再同期してから削除してください。"
                            ),
                        )
                    attempted_folder_ids.append(mapping.drive_folder_id)
                    self._request(
                        "PATCH",
                        f"{_DRIVE_FILES}/{quote(mapping.drive_folder_id, safe='')}",
                        params={
                            "fields": "id,trashed",
                            "supportsAllDrives": "true",
                        },
                        json={"trashed": True},
                    )
                    if {
                        str(child.get("id"))
                        for child in self._list_drive_children(
                            mapping.drive_folder_id,
                            include_trashed=True,
                        )
                        if child.get("id")
                    } != child_snapshot:
                        raise AppError(
                            status=409,
                            code="DRIVE_FOLDER_NOT_EMPTY",
                            title="Drive folder was not empty",
                            detail=(
                                "削除処理中にGoogle Drive側へファイルが追加されました。"
                                "フォルダを復元したため、内容を確認してください。"
                            ),
                        )
                session.execute(
                    delete(DocumentFolderDriveMapping).where(
                        DocumentFolderDriveMapping.google_subject == subject,
                        DocumentFolderDriveMapping.document_folder_id == folder_id,
                    )
                )
                session.delete(folder)
                session.commit()
            except Exception as exc:
                session.rollback()
                compensation_failed = False
                for drive_folder_id in reversed(attempted_folder_ids):
                    try:
                        current = self._drive_item(drive_folder_id)
                        if current is not None and current.get("trashed") is True:
                            self._request(
                                "PATCH",
                                f"{_DRIVE_FILES}/{quote(drive_folder_id, safe='')}",
                                params={
                                    "fields": "id,trashed",
                                    "supportsAllDrives": "true",
                                },
                                json={"trashed": False},
                            )
                    except Exception:
                        compensation_failed = True
                if compensation_failed:
                    raise AppError(
                        status=409,
                        code="DRIVE_FOLDER_DELETE_PARTIAL",
                        title="Drive folder deletion was partially applied",
                        detail=(
                            "一部のGoogle Driveフォルダを自動復元できませんでした。"
                            "再同期して競合の詳細を確認してください。"
                        ),
                    ) from exc
                raise

    def move_library_document(
        self,
        document_id: str,
        folder_id: str | None,
        *,
        expected_version: int | None = None,
        expected_subject: str | None = None,
    ) -> dict[str, Any] | None:
        subject = self._require_subject(expected_subject)
        with self._drive_operation(subject), self._database.session() as session:
            ledger = session.get(LedgerDocument, document_id)
            if ledger is None or ledger.library_deleted_at is not None:
                raise AppError(
                    status=404,
                    code="DOCUMENT_NOT_FOUND",
                    title="Document was not found",
                    detail="書類が見つかりません。",
                )
            if (
                expected_version is not None
                and ledger.library_location_version != expected_version
            ):
                raise AppError(
                    status=409,
                    code="DOCUMENT_LOCATION_CONFLICT",
                    title="Document location changed",
                    detail=(
                        "別の操作で保存場所が変更されています。"
                        "最新状態を読み込んでください。"
                    ),
                )
            target_folder = (
                session.get(DocumentFolder, folder_id)
                if folder_id is not None
                else None
            )
            if folder_id is not None and target_folder is None:
                raise AppError(
                    status=404,
                    code="DOCUMENT_FOLDER_NOT_FOUND",
                    title="Document folder was not found",
                    detail="移動先のフォルダが見つかりません。",
                )
            if not ledger.spreadsheet_id:
                if expected_version is None:
                    return None
                ledger.library_folder_id = folder_id
                ledger.library_location_version += 1
                session.commit()
                return {
                    "documentId": ledger.id,
                    "folderId": ledger.library_folder_id,
                    "locationVersion": ledger.library_location_version,
                }
            if (
                ledger.drive_google_subject is not None
                and ledger.drive_google_subject != subject
            ):
                raise self._drive_document_account_mismatch()
            if ledger.drive_google_subject is None and ledger.spreadsheet_id:
                raise self._drive_document_account_ambiguous()
            item = self._drive_item(ledger.spreadsheet_id)
            if item is None or item.get("trashed") is True:
                raise AppError(
                    status=409,
                    code="DRIVE_SPREADSHEET_NOT_FOUND",
                    title="Drive spreadsheet was not found",
                    detail="Google Drive上のスプレッドシートが見つかりません。",
                )
            current_parents = self._string_list(item.get("parents"))
            if len(current_parents) != 1:
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                    title="Drive document location is ambiguous",
                    detail=(
                        "Google Drive側の現在位置を一意に確認できません。"
                        "再同期してから移動してください。"
                    ),
                )
            actual_parent = current_parents[0]
            baseline_parent = ledger.drive_folder_id
            baseline_library_folder_id = ledger.library_folder_id
            baseline_location_version = ledger.library_location_version
            root_id = ledger.drive_root_folder_id or self._effective_root_for_document(
                session,
                subject,
                ledger,
            )
            if not root_id:
                raise AppError(
                    status=409,
                    code="DRIVE_OUTPUT_FOLDER_REQUIRED",
                    title="Drive output folder is required",
                    detail="この書類のGoogle Drive保存先を確認できません。",
                )
            target_parent = root_id
            created_target_folder_ids: list[str] = []
            try:
                if folder_id is not None:
                    assert target_folder is not None
                    mapping = session.scalar(
                        select(DocumentFolderDriveMapping).where(
                            DocumentFolderDriveMapping.google_subject == subject,
                            DocumentFolderDriveMapping.document_folder_id == folder_id,
                            DocumentFolderDriveMapping.drive_root_folder_id == root_id,
                        )
                    )
                    if mapping is None and (
                        baseline_parent is None or actual_parent != baseline_parent
                    ):
                        raise AppError(
                            status=409,
                            code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                            title="Drive document location changed",
                            detail=(
                                "Google Drive側と独自ライブラリ側で保存場所が"
                                "別々に変更されています。"
                                "再同期して現在位置を確認してください。"
                            ),
                        )
                    if mapping is None:
                        mapping, _created = self._ensure_library_folder_mapping(
                            session,
                            subject,
                            target_folder,
                            root_id,
                            created_remote_folder_ids=created_target_folder_ids,
                        )
                    target_parent = mapping.drive_folder_id
                if (
                    baseline_parent is None
                    or (
                        actual_parent != baseline_parent
                        and actual_parent != target_parent
                    )
                ):
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                        title="Drive document location changed",
                        detail=(
                            "Google Drive側と独自ライブラリ側で保存場所が別々に変更されています。"
                            "再同期して現在位置を確認してください。"
                        ),
                    )
                if actual_parent != target_parent:
                    latest = self._drive_item(ledger.spreadsheet_id)
                    if not self._drive_item_matches_snapshot(latest, item):
                        raise AppError(
                            status=409,
                            code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                            title="Drive document changed during move",
                            detail="移動直前にGoogle Drive側の保存場所が変更されました。",
                        )
                    self._move_drive_item(
                        ledger.spreadsheet_id,
                        target_parent,
                        current_parents,
                    )
                library_folder_condition = (
                    LedgerDocument.library_folder_id.is_(None)
                    if baseline_library_folder_id is None
                    else LedgerDocument.library_folder_id == baseline_library_folder_id
                )
                values: dict[str, Any] = {
                    "drive_root_folder_id": root_id,
                    "drive_folder_id": target_parent,
                    "drive_folder_path": self._library_folder_path(session, folder_id),
                }
                next_location_version = baseline_location_version
                if expected_version is not None:
                    next_location_version += 1
                    values.update(
                        library_folder_id=folder_id,
                        library_location_version=next_location_version,
                    )
                updated = session.execute(
                    update(LedgerDocument)
                    .where(
                        LedgerDocument.id == document_id,
                        LedgerDocument.library_deleted_at.is_(None),
                        LedgerDocument.library_location_version
                        == baseline_location_version,
                        library_folder_condition,
                    )
                    .values(**values)
                )
                if updated.rowcount != 1:
                    raise AppError(
                        status=409,
                        code="DOCUMENT_LOCATION_CONFLICT",
                        title="Document location changed",
                        detail=(
                            "Google Driveへの反映中に別の操作で保存場所が変更されました。"
                            "Drive側を元に戻したため、最新状態で再試行してください。"
                        ),
                    )
                session.commit()
            except Exception as exc:
                session.rollback()
                compensated = self._compensate_library_document_move(
                    ledger.spreadsheet_id,
                    original_parent=actual_parent,
                    attempted_parent=target_parent,
                    created_folder_ids=created_target_folder_ids,
                )
                if not compensated:
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_MOVE_PARTIAL",
                        title="Drive document move was partially applied",
                        detail=(
                            "Google Drive側の移動を自動で元に戻せませんでした。"
                            "再同期して現在位置を確認してください。"
                        ),
                    ) from exc
                if isinstance(exc, AppError):
                    raise
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_MOVE_ROLLED_BACK",
                    title="Drive document move was rolled back",
                    detail=(
                        "ローカルへの保存に失敗したため、Google Drive側の移動を"
                        "元に戻しました。最新状態を読み込んで再試行してください。"
                    ),
                ) from exc
            if expected_version is None:
                return None
            return {
                "documentId": document_id,
                "folderId": folder_id,
                "locationVersion": next_location_version,
            }

    def mark_document_sync(
        self,
        document_id: str,
        *,
        status: str,
        error: str | None,
    ) -> None:
        with self._database.session() as session:
            ledger = session.get(LedgerDocument, document_id)
            if ledger is None:
                return
            ledger.spreadsheet_sync_status = status
            ledger.spreadsheet_sync_error = error
            session.commit()

    def reconcile_document_name(
        self,
        document_id: str,
        desired_name: str,
        *,
        operation_id: str,
        expected_subject: str | None = None,
    ) -> str:
        normalized_desired = self._drive_document_name(desired_name)
        if normalized_desired is None or normalized_desired != desired_name:
            raise AppError(
                status=422,
                code="DRIVE_DOCUMENT_NAME_INVALID",
                title="Drive document name is invalid",
                detail="Google Driveへ反映する書類名が不正です。",
            )
        with self._drive_operation(expected_subject) as subject:
            with self._database.session() as session:
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None or ledger.library_deleted_at is not None:
                    raise AppError(
                        status=404,
                        code="DOCUMENT_NOT_FOUND",
                        title="Document was not found",
                        detail="書類が見つかりません。",
                    )
                self._assert_ledger_spreadsheet_in_current_roots(
                    session,
                    ledger,
                    subject,
                )
                intent = session.get(SpreadsheetOutputOperation, operation_id)
                if (
                    intent is None
                    or intent.document_id != document_id
                    or intent.google_subject != subject
                    or intent.spreadsheet_id != ledger.spreadsheet_id
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_NAME_OPERATION_CONFLICT",
                        title="Spreadsheet name operation conflicts",
                        detail="書類名の同期処理情報が一致しません。",
                    )
                spreadsheet_id = ledger.spreadsheet_id
                baseline_name = intent.drive_name
            if spreadsheet_id is None:
                raise AppError(
                    status=409,
                    code="DOCUMENT_SPREADSHEET_NOT_READY",
                    title="Document spreadsheet is not ready",
                    detail="対象のGoogleスプレッドシートを確認できません。",
                )
            current = self._drive_item(spreadsheet_id)
            if (
                current is None
                or current.get("mimeType") != _SPREADSHEET_MIME
                or current.get("trashed") is True
            ):
                raise AppError(
                    status=409,
                    code="DRIVE_SPREADSHEET_NOT_FOUND",
                    title="Drive spreadsheet was not found",
                    detail="Google Drive上のスプレッドシートが見つかりません。",
                )
            current_name = self._drive_document_name(str(current.get("name") or ""))
            current_parents = self._string_list(current.get("parents"))
            if len(current_parents) != 1:
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_LOCATION_CONFLICT",
                    title="Drive document location is ambiguous",
                    detail="Google Drive側の保存場所を一意に確認できません。",
                )
            if current_name not in {baseline_name, normalized_desired}:
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_NAME_CONFLICT",
                    title="Drive document name changed",
                    detail=(
                        "Google Drive側と独自ライブラリ側で名前が別々に変更されています。"
                        "再同期して現在の名前を確認してください。"
                    ),
                )
            renamed = current
            if current_name == baseline_name:
                latest = self._drive_item(spreadsheet_id)
                if not self._drive_item_matches_snapshot(latest, current):
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_NAME_CONFLICT",
                        title="Drive document changed during rename",
                        detail="名前変更直前にGoogle Drive側の書類が変更されました。",
                    )
                renamed = self._request(
                    "PATCH",
                    f"{_DRIVE_FILES}/{quote(spreadsheet_id, safe='')}",
                    params={
                        "fields": (
                            "id,name,mimeType,parents,modifiedTime,trashed,"
                            "explicitlyTrashed,appProperties"
                        ),
                        "supportsAllDrives": "true",
                    },
                    json={"name": normalized_desired},
                )
                if (
                    str(renamed.get("id") or "") != spreadsheet_id
                    or self._drive_document_name(str(renamed.get("name") or ""))
                    != normalized_desired
                    or self._string_list(renamed.get("parents")) != current_parents
                    or renamed.get("trashed") is True
                ):
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_NAME_CONFLICT",
                        title="Drive document rename could not be verified",
                        detail="Google Drive側の名前変更結果を確認できませんでした。",
                    )
            final_item = self._drive_item(spreadsheet_id)
            if (
                final_item is None
                or not self._drive_item_matches_snapshot(final_item, renamed)
                or self._drive_document_name(str(final_item.get("name") or ""))
                != normalized_desired
                or self._string_list(final_item.get("parents")) != current_parents
                or final_item.get("trashed") is True
            ):
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_NAME_CONFLICT",
                    title="Drive document changed after rename",
                    detail="名前変更処理中にGoogle Drive側の書類が変更されました。",
                )
            with self._database.session() as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                ledger = session.get(LedgerDocument, document_id)
                intent = session.get(SpreadsheetOutputOperation, operation_id)
                if (
                    ledger is None
                    or ledger.spreadsheet_id != spreadsheet_id
                    or (ledger.document_name or ledger.library_name)
                    != normalized_desired
                    or intent is None
                    or intent.document_id != document_id
                    or intent.spreadsheet_id != spreadsheet_id
                ):
                    raise AppError(
                        status=409,
                        code="SPREADSHEET_NAME_OPERATION_CONFLICT",
                        title="Spreadsheet name operation changed",
                        detail="名前同期中に書類が変更されました。再試行してください。",
                    )
                intent.drive_name = normalized_desired
                session.commit()
            return normalized_desired

    def rename_spreadsheet(
        self,
        spreadsheet_id: str,
        name: str,
        expected_subject: str | None = None,
        *,
        baseline_name: str | None = None,
        rename_result_sheet: bool = False,
    ) -> str:
        with self._drive_operation(expected_subject) as subject:
            with self._database.session() as session:
                ledger = session.scalar(
                    select(LedgerDocument).where(
                        LedgerDocument.spreadsheet_id == spreadsheet_id
                    )
                )
                if ledger is not None:
                    self._assert_ledger_spreadsheet_in_current_roots(
                        session,
                        ledger,
                        subject,
                    )
                    confirmed = (
                        session.scalar(
                            select(ConfirmedResult).where(
                                ConfirmedResult.job_id == ledger.current_job_id
                            )
                        )
                        if ledger.current_job_id
                        else None
                    )
                    if baseline_name is None:
                        baseline_name = (
                            ledger.document_name
                            or ledger.library_name
                            or (
                                confirmed.spreadsheet_name
                                if confirmed is not None
                                else None
                            )
                        )
            current = self._drive_item(spreadsheet_id)
            if current is None or current.get("trashed") is True:
                raise AppError(
                    status=409,
                    code="DRIVE_SPREADSHEET_NOT_FOUND",
                    title="Drive spreadsheet was not found",
                    detail="Google Drive上のスプレッドシートが見つかりません。",
                )
            current_name = str(current.get("name") or "")
            if (
                baseline_name
                and current_name != baseline_name
                and current_name != name
            ):
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_NAME_CONFLICT",
                    title="Drive document name changed",
                    detail=(
                        "Google Drive側と独自ライブラリ側で名前が別々に変更されています。"
                        "再同期して現在の名前を確認してください。"
                    ),
                )
            sheet_snapshot = (
                self._single_result_sheet(spreadsheet_id)
                if rename_result_sheet
                else None
            )
            if (
                current_name == name
                and (sheet_snapshot is None or sheet_snapshot[1] == name)
            ):
                return current_name
            latest = self._drive_item(spreadsheet_id)
            if not self._drive_item_matches_snapshot(latest, current):
                raise AppError(
                    status=409,
                    code="DRIVE_DOCUMENT_NAME_CONFLICT",
                    title="Drive document changed during rename",
                    detail="名前変更直前にGoogle Drive側の書類が変更されました。",
                )
            expected_parents = self._string_list(current.get("parents"))
            if not rename_result_sheet:
                renamed = self._patch_drive_item_name(spreadsheet_id, name)
                if (
                    str(renamed.get("id") or "") != spreadsheet_id
                    or renamed.get("name") != name
                    or self._string_list(renamed.get("parents")) != expected_parents
                    or renamed.get("trashed") is True
                ):
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_NAME_CONFLICT",
                        title="Drive document rename could not be verified",
                        detail="Google Drive側の名前変更結果を確認できませんでした。",
                    )
                final_item = self._drive_item(spreadsheet_id)
                if (
                    final_item is None
                    or not self._drive_item_matches_snapshot(final_item, renamed)
                    or final_item.get("name") != name
                    or self._string_list(final_item.get("parents")) != expected_parents
                    or final_item.get("trashed") is True
                ):
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_NAME_CONFLICT",
                        title="Drive document changed after rename",
                        detail="名前変更処理中にGoogle Drive側の書類が変更されました。",
                    )
                return name
            renamed = current
            drive_changed = False
            sheet_changed = False
            try:
                if current_name != name:
                    renamed = self._patch_drive_item_name(spreadsheet_id, name)
                    if (
                        str(renamed.get("id") or "") != spreadsheet_id
                        or renamed.get("name") != name
                        or self._string_list(renamed.get("parents")) != expected_parents
                        or renamed.get("trashed") is True
                    ):
                        raise AppError(
                            status=409,
                            code="DRIVE_DOCUMENT_NAME_CONFLICT",
                            title="Drive document rename could not be verified",
                            detail="Google Drive側の名前変更結果を確認できませんでした。",
                        )
                    drive_changed = True
                if sheet_snapshot is not None and sheet_snapshot[1] != name:
                    self._rename_result_sheet_tab(
                        spreadsheet_id,
                        sheet_id=sheet_snapshot[0],
                        title=name,
                    )
                    sheet_changed = True
                final_item = self._drive_item(spreadsheet_id)
                drive_snapshot_matches = (
                    final_item is not None
                    and final_item.get("name") == name
                    and self._string_list(final_item.get("parents")) == expected_parents
                    and final_item.get("trashed") is not True
                )
                if not drive_snapshot_matches:
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_NAME_CONFLICT",
                        title="Drive document changed after rename",
                        detail="名前変更処理中にGoogle Drive側の書類が変更されました。",
                    )
                if rename_result_sheet and self._single_result_sheet(spreadsheet_id)[1] != name:
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_NAME_CONFLICT",
                        title="Drive result sheet rename could not be verified",
                        detail="Googleスプレッドシート内部のsheet名を確認できませんでした。",
                    )
                return name
            except Exception as exc:
                compensation_failed = False
                if sheet_changed and sheet_snapshot is not None:
                    try:
                        self._rename_result_sheet_tab(
                            spreadsheet_id,
                            sheet_id=sheet_snapshot[0],
                            title=sheet_snapshot[1],
                        )
                    except Exception:
                        compensation_failed = True
                if drive_changed:
                    try:
                        self._patch_drive_item_name(spreadsheet_id, current_name)
                    except Exception:
                        compensation_failed = True
                if compensation_failed:
                    raise AppError(
                        status=409,
                        code="DRIVE_DOCUMENT_RENAME_PARTIAL",
                        title="Drive document rename was partially applied",
                        detail=(
                            "Google Driveファイル名またはsheet名を元に戻せませんでした。"
                            "再同期して現在名を確認してください。"
                        ),
                    ) from exc
                raise

    def _patch_drive_item_name(self, spreadsheet_id: str, name: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"{_DRIVE_FILES}/{quote(spreadsheet_id, safe='')}",
            params={
                "fields": (
                    "id,name,mimeType,parents,modifiedTime,trashed,"
                    "explicitlyTrashed,appProperties"
                ),
                "supportsAllDrives": "true",
            },
            json={"name": name},
        )

    def _single_result_sheet(self, spreadsheet_id: str) -> tuple[int, str]:
        payload = self._request(
            "GET",
            f"{_SHEETS_SPREADSHEETS}/{quote(spreadsheet_id, safe='')}",
            params={"fields": "sheets(properties(sheetId,title,index))"},
        )
        raw_sheets = payload.get("sheets")
        if not isinstance(raw_sheets, list) or len(raw_sheets) != 1:
            raise AppError(
                status=409,
                code="DRIVE_SPREADSHEET_SHEET_STRUCTURE_CONFLICT",
                title="Drive spreadsheet sheet structure conflicts",
                detail="結果sheetが1枚ではないため、安全に名前を同期できません。",
            )
        properties = raw_sheets[0].get("properties") if isinstance(raw_sheets[0], dict) else None
        sheet_id = properties.get("sheetId") if isinstance(properties, dict) else None
        title = properties.get("title") if isinstance(properties, dict) else None
        if not isinstance(sheet_id, int) or not isinstance(title, str):
            raise AppError(
                status=409,
                code="DRIVE_SPREADSHEET_SHEET_STRUCTURE_CONFLICT",
                title="Drive spreadsheet sheet structure conflicts",
                detail="結果sheetのidentityを確認できません。",
            )
        return sheet_id, title

    def _rename_result_sheet_tab(
        self,
        spreadsheet_id: str,
        *,
        sheet_id: int,
        title: str,
    ) -> None:
        self._request(
            "POST",
            f"{_SHEETS_SPREADSHEETS}/{quote(spreadsheet_id, safe='')}:batchUpdate",
            json={
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "title": title},
                            "fields": "title",
                        }
                    }
                ]
            },
        )

    def _library_roots(
        self,
        session: Session,
        subject: str,
    ) -> list[tuple[str, str | None]]:
        account = session.get(GoogleOAuthAccount, subject)
        if account is None:
            raise self._not_connected()
        candidates: list[tuple[str | None, str | None]] = [
            (
                account.default_output_folder_id,
                account.default_output_folder_path or account.default_output_folder_name,
            )
        ]
        candidates.extend(
            (
                selected.folder_id,
                selected.folder_path or selected.folder_name,
            )
            for selected in session.scalars(
                select(TemplateOutputFolder)
                .where(TemplateOutputFolder.google_subject == subject)
                .order_by(TemplateOutputFolder.template_id)
            )
        )
        roots: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for folder_id, folder_name in candidates:
            if not folder_id or folder_id in seen:
                continue
            seen.add(folder_id)
            roots.append((folder_id, folder_name))
        return roots

    def _ensure_library_folder_mapping(
        self,
        session: Session,
        subject: str,
        folder: DocumentFolder,
        root_id: str,
        *,
        result: dict[str, Any] | None = None,
        trail: set[str] | None = None,
        verified_folder_ids: set[str] | None = None,
        created_remote_folder_ids: list[str] | None = None,
    ) -> tuple[DocumentFolderDriveMapping, bool]:
        active_trail = set() if trail is None else set(trail)
        if folder.id in active_trail:
            raise AppError(
                status=409,
                code="DOCUMENT_FOLDER_CYCLE",
                title="Document folder hierarchy contains a cycle",
                detail="フォルダ階層に循環があるためGoogle Driveへ同期できません。",
            )
        active_trail.add(folder.id)

        desired_parent = root_id
        if folder.parent_id is not None:
            parent = session.get(DocumentFolder, folder.parent_id)
            if parent is None:
                raise AppError(
                    status=409,
                    code="DOCUMENT_FOLDER_PARENT_NOT_FOUND",
                    title="Document folder parent was not found",
                    detail="親フォルダが見つからないためGoogle Driveへ同期できません。",
                )
            parent_mapping = None
            if verified_folder_ids is not None and parent.id in verified_folder_ids:
                parent_mapping = session.scalar(
                    select(DocumentFolderDriveMapping).where(
                        DocumentFolderDriveMapping.google_subject == subject,
                        DocumentFolderDriveMapping.document_folder_id == parent.id,
                        DocumentFolderDriveMapping.drive_root_folder_id == root_id,
                    )
                )
            if parent_mapping is None:
                parent_mapping, _created = self._ensure_library_folder_mapping(
                    session,
                    subject,
                    parent,
                    root_id,
                    result=result,
                    trail=active_trail,
                    verified_folder_ids=verified_folder_ids,
                    created_remote_folder_ids=created_remote_folder_ids,
                )
            desired_parent = parent_mapping.drive_folder_id

        mapping = session.scalar(
            select(DocumentFolderDriveMapping).where(
                DocumentFolderDriveMapping.google_subject == subject,
                DocumentFolderDriveMapping.document_folder_id == folder.id,
                DocumentFolderDriveMapping.drive_root_folder_id == root_id,
            )
        )
        if mapping is not None and result is not None:
            skipped = result.get("_skipMappingIds")
            if isinstance(skipped, list) and mapping.id in skipped:
                return mapping, False

        if mapping is None:
            children_cache = (
                result.get("_driveChildrenCache") if result is not None else None
            )
            cached_children = (
                children_cache.get(desired_parent)
                if isinstance(children_cache, dict)
                else None
            )
            children = (
                list(cached_children)
                if isinstance(cached_children, list)
                else [
                    item
                    for item in self._list_drive_children(desired_parent)
                    if item.get("mimeType") == _FOLDER_MIME
                ]
            )
            if isinstance(children_cache, dict) and cached_children is None:
                children_cache[desired_parent] = children
            tagged = [
                item
                for item in children
                if isinstance(item.get("appProperties"), dict)
                and item["appProperties"].get("llmOcrLibraryFolderId") == folder.id
                and item["appProperties"].get("llmOcrDriveRootId") == root_id
            ]
            if len(tagged) > 1:
                raise self._drive_folder_mapping_ambiguous(folder.name)
            if len(tagged) == 1:
                item = tagged[0]
                created_remote = False
            else:
                same_name = [
                    item
                    for item in children
                    if (
                        self._drive_folder_name(str(item.get("name") or ""))
                        or ""
                    ).casefold()
                    == folder.normalized_name
                ]
                conflicting_tag = any(
                    isinstance(item.get("appProperties"), dict)
                    and item["appProperties"].get("llmOcrLibraryFolderId")
                    for item in same_name
                )
                adoptable = [
                    item
                    for item in same_name
                    if not isinstance(item.get("appProperties"), dict)
                    or not item["appProperties"].get("llmOcrLibraryFolderId")
                ]
                adoptable = [
                    item
                    for item in adoptable
                    if session.scalar(
                        select(DocumentFolderDriveMapping.id).where(
                            DocumentFolderDriveMapping.google_subject == subject,
                            DocumentFolderDriveMapping.drive_folder_id
                            == str(item.get("id")),
                        )
                    )
                    is None
                ]
                if conflicting_tag or len(adoptable) > 1:
                    raise self._drive_folder_mapping_ambiguous(folder.name)
            if not tagged and len(adoptable) == 1:
                fresh_children = [
                    candidate
                    for candidate in self._list_drive_children(desired_parent)
                    if candidate.get("mimeType") == _FOLDER_MIME
                ]
                fresh_same_name = [
                    candidate
                    for candidate in fresh_children
                    if (
                        self._drive_folder_name(
                            str(candidate.get("name") or "")
                        )
                        or ""
                    ).casefold()
                    == folder.normalized_name
                ]
                if (
                    len(fresh_same_name) != 1
                    or fresh_same_name[0].get("id") != adoptable[0].get("id")
                ):
                    raise self._drive_folder_mapping_ambiguous(folder.name)
                item = self._tag_library_drive_folder(
                    fresh_same_name[0],
                    folder_id=folder.id,
                    root_id=root_id,
                )
                if isinstance(children_cache, dict):
                    children_cache[desired_parent] = [
                        item if child.get("id") == item.get("id") else child
                        for child in children
                    ]
                created_remote = False
            elif not tagged:
                item = self._create_library_drive_folder(
                    folder,
                    root_id=root_id,
                    parent_id=desired_parent,
                )
                fresh_same_name = [
                    candidate
                    for candidate in self._list_drive_children(desired_parent)
                    if candidate.get("mimeType") == _FOLDER_MIME
                    and (
                        self._drive_folder_name(
                            str(candidate.get("name") or "")
                        )
                        or ""
                    ).casefold()
                    == folder.normalized_name
                ]
                if (
                    len(fresh_same_name) != 1
                    or fresh_same_name[0].get("id") != item.get("id")
                ):
                    try:
                        self._request(
                            "PATCH",
                            f"{_DRIVE_FILES}/{quote(str(item['id']), safe='')}",
                            params={
                                "fields": "id,trashed",
                                "supportsAllDrives": "true",
                            },
                            json={"trashed": True},
                        )
                    except Exception as exc:
                        raise AppError(
                            status=409,
                            code="DRIVE_FOLDER_CREATE_PARTIAL",
                            title="Drive folder creation could not be compensated",
                            detail=(
                                "同名フォルダの競合を検出しましたが、"
                                "作成したフォルダを自動でゴミ箱へ移動できませんでした。"
                            ),
                        ) from exc
                    raise self._drive_folder_mapping_ambiguous(folder.name)
                if isinstance(children_cache, dict):
                    children.append(item)
                created_remote = True
                if created_remote_folder_ids is not None:
                    created_remote_folder_ids.append(str(item["id"]))
            mapping = DocumentFolderDriveMapping(
                google_subject=subject,
                document_folder_id=folder.id,
                drive_root_folder_id=root_id,
                drive_folder_id=str(item["id"]),
                drive_name=str(item.get("name") or folder.name),
                drive_parent_folder_id=desired_parent,
            )
            self._update_mapping_snapshot(mapping, item)
            session.add(mapping)
            session.flush()
            if verified_folder_ids is not None:
                verified_folder_ids.add(folder.id)
            return mapping, created_remote

        item_cache = result.get("_driveItemCache") if result is not None else None
        cached_item = (
            item_cache.get(mapping.drive_folder_id)
            if isinstance(item_cache, dict)
            else None
        )
        drive_item = (
            cached_item
            if isinstance(cached_item, dict)
            else self._drive_item(mapping.drive_folder_id)
        )
        if drive_item is None or drive_item.get("trashed") is True:
            if result is not None:
                self._record_library_conflict(
                    result,
                    code="DRIVE_FOLDER_REMOVED",
                    resource_id=folder.id,
                    resource_name=folder.name,
                    detail=(
                        "Drive側のフォルダが削除されていたため、"
                        "ローカルの内容を保持してフォルダを再作成しました。"
                    ),
                    mapping_id=mapping.id,
                    drive_root_id=root_id,
                )
            previous_drive_folder_id = mapping.drive_folder_id
            sibling_folders = [
                candidate
                for candidate in self._list_drive_children(desired_parent)
                if candidate.get("mimeType") == _FOLDER_MIME
            ]
            replacement_candidates = [
                candidate
                for candidate in sibling_folders
                if isinstance(candidate.get("appProperties"), dict)
                and candidate["appProperties"].get("llmOcrLibraryFolderId")
                == folder.id
                and candidate["appProperties"].get("llmOcrDriveRootId") == root_id
            ]
            if len(replacement_candidates) > 1:
                raise self._drive_folder_mapping_ambiguous(folder.name)
            if replacement_candidates:
                drive_item = replacement_candidates[0]
                created_replacement = False
            else:
                adoptable_replacements = [
                    candidate
                    for candidate in sibling_folders
                    if (
                        self._drive_folder_name(
                            str(candidate.get("name") or "")
                        )
                        or ""
                    ).casefold()
                    == folder.normalized_name
                    and (
                        not isinstance(candidate.get("appProperties"), dict)
                        or not candidate["appProperties"].get("llmOcrLibraryFolderId")
                    )
                ]
                if len(adoptable_replacements) > 1:
                    raise self._drive_folder_mapping_ambiguous(folder.name)
                if adoptable_replacements:
                    drive_item = self._tag_library_drive_folder(
                        adoptable_replacements[0],
                        folder_id=folder.id,
                        root_id=root_id,
                    )
                    created_replacement = False
                else:
                    drive_item = self._create_library_drive_folder(
                        folder,
                        root_id=root_id,
                        parent_id=desired_parent,
                    )
                    created_replacement = True
            mapping.drive_folder_id = str(drive_item["id"])
            self._update_mapping_snapshot(mapping, drive_item)
            if isinstance(item_cache, dict):
                item_cache[mapping.drive_folder_id] = drive_item
            if result is not None:
                recreated = result.setdefault("_recreatedFolderParents", {})
                if isinstance(recreated, dict):
                    recreated[previous_drive_folder_id] = mapping.drive_folder_id
                recreated_targets = result.setdefault("_recreatedFolderTargets", {})
                if isinstance(recreated_targets, dict):
                    recreated_targets[f"{root_id}:{folder.id}"] = mapping.drive_folder_id
            if verified_folder_ids is not None:
                verified_folder_ids.add(folder.id)
            return mapping, created_replacement

        parents = self._string_list(drive_item.get("parents"))
        actual_parent = parents[0] if parents else ""
        actual_name = str(drive_item.get("name") or "")
        remote_name_changed = actual_name != mapping.drive_name
        local_name_changed = folder.name != mapping.drive_name
        remote_parent_changed = actual_parent != mapping.drive_parent_folder_id
        local_parent_changed = desired_parent != mapping.drive_parent_folder_id
        name_conflict = (
            remote_name_changed
            and local_name_changed
            and actual_name != folder.name
        )
        parent_conflict = (
            remote_parent_changed
            and local_parent_changed
            and actual_parent != desired_parent
        )
        if actual_name == folder.name and actual_parent == desired_parent:
            self._update_mapping_snapshot(mapping, drive_item)
            if verified_folder_ids is not None:
                verified_folder_ids.add(folder.id)
            return mapping, False
        remote_only_change = (
            (remote_name_changed and not local_name_changed)
            or (remote_parent_changed and not local_parent_changed)
        )
        if name_conflict or parent_conflict or remote_only_change:
            if result is not None:
                self._record_library_conflict(
                    result,
                    code=(
                        "BOTH_MODIFIED"
                        if name_conflict or parent_conflict
                        else "DRIVE_FOLDER_CHANGED"
                    ),
                    resource_id=folder.id,
                    resource_name=folder.name,
                    detail=(
                        "Drive側と独自ライブラリ側の両方が変更されています。"
                        if name_conflict or parent_conflict
                        else "Drive側の変更を独自ライブラリへ取り込めませんでした。"
                    ),
                    mapping_id=mapping.id,
                    drive_root_id=root_id,
                )
                return mapping, False
            raise AppError(
                status=409,
                code="DOCUMENT_FOLDER_SYNC_CONFLICT",
                title="Document folder sync conflict",
                detail="Drive側にも未同期の変更があるためフォルダを更新できません。",
            )

        latest_drive_item = self._drive_item(mapping.drive_folder_id)
        if not self._drive_item_matches_snapshot(latest_drive_item, drive_item):
            if result is not None:
                self._record_library_conflict(
                    result,
                    code="DRIVE_FOLDER_CHANGED_DURING_SYNC",
                    resource_id=folder.id,
                    resource_name=folder.name,
                    detail=(
                        "同期処理中にGoogle Drive側のフォルダが変更されたため、"
                        "ローカルの変更は反映していません。"
                    ),
                    mapping_id=mapping.id,
                    drive_root_id=root_id,
                )
                return mapping, False
            raise AppError(
                status=409,
                code="DOCUMENT_FOLDER_SYNC_CONFLICT",
                title="Document folder changed during synchronization",
                detail="同期処理中にDrive側のフォルダが変更されました。",
            )
        assert latest_drive_item is not None
        drive_item = latest_drive_item
        if actual_name != folder.name:
            drive_item = self._request(
                "PATCH",
                f"{_DRIVE_FILES}/{quote(mapping.drive_folder_id, safe='')}",
                params={
                    "fields": (
                        "id,name,mimeType,parents,modifiedTime,trashed,"
                        "explicitlyTrashed,appProperties"
                    ),
                    "supportsAllDrives": "true",
                },
                json={"name": folder.name},
            )
            parents = self._string_list(drive_item.get("parents"))
            if (
                str(drive_item.get("name") or "") != folder.name
                or parents != [actual_parent]
                or drive_item.get("trashed") is True
            ):
                raise AppError(
                    status=409,
                    code="DRIVE_FOLDER_CHANGED_DURING_SYNC",
                    title="Drive folder changed during synchronization",
                    detail="Drive側の名前変更結果を一意に確認できませんでした。",
                )
        if actual_parent != desired_parent:
            latest_drive_item = self._drive_item(mapping.drive_folder_id)
            if not self._drive_item_matches_snapshot(latest_drive_item, drive_item):
                raise AppError(
                    status=409,
                    code="DRIVE_FOLDER_CHANGED_DURING_SYNC",
                    title="Drive folder changed during synchronization",
                    detail="移動直前にDrive側のフォルダが変更されました。",
                )
            drive_item = self._move_drive_item(
                mapping.drive_folder_id,
                desired_parent,
                [actual_parent] if actual_parent else [],
            )
        self._update_mapping_snapshot(mapping, drive_item)
        if isinstance(item_cache, dict):
            item_cache[mapping.drive_folder_id] = drive_item
        if verified_folder_ids is not None:
            verified_folder_ids.add(folder.id)
        return mapping, False

    def _import_drive_folder_tree(
        self,
        subject: str,
        root_id: str,
        result: dict[str, Any],
        *,
        excluded_folder_ids: set[str] | None = None,
    ) -> None:
        excluded = excluded_folder_ids or set()
        with self._database.session() as session:
            queue: deque[tuple[str, str | None]] = deque(((root_id, None),))
            visited: set[str] = set()
            while queue:
                drive_parent_id, local_parent_id = queue.popleft()
                if drive_parent_id in visited:
                    continue
                visited.add(drive_parent_id)
                drive_items = self._list_drive_children(drive_parent_id)
                item_cache = result.get("_driveItemCache")
                if isinstance(item_cache, dict):
                    for candidate in drive_items:
                        candidate_id = candidate.get("id")
                        if isinstance(candidate_id, str) and candidate_id:
                            item_cache[candidate_id] = candidate
                children_cache = result.get("_driveChildrenCache")
                if isinstance(children_cache, dict):
                    children_cache[drive_parent_id] = [
                        candidate
                        for candidate in drive_items
                        if candidate.get("mimeType") == _FOLDER_MIME
                    ]
                folder_name_counts: dict[str, int] = {}
                for candidate in drive_items:
                    if candidate.get("mimeType") != _FOLDER_MIME:
                        continue
                    candidate_name = self._drive_folder_name(
                        str(candidate.get("name") or "")
                    )
                    if candidate_name is not None:
                        normalized = candidate_name.casefold()
                        folder_name_counts[normalized] = (
                            folder_name_counts.get(normalized, 0) + 1
                        )
                ambiguous_names = {
                    name for name, count in folder_name_counts.items() if count > 1
                }
                for item in drive_items:
                    if item.get("mimeType") != _FOLDER_MIME:
                        continue
                    drive_folder_id = str(item.get("id") or "")
                    if (
                        not drive_folder_id
                        or drive_folder_id in visited
                        or drive_folder_id in excluded
                    ):
                        continue
                    candidate_name = self._drive_folder_name(
                        str(item.get("name") or "")
                    )
                    if (
                        candidate_name is not None
                        and candidate_name.casefold() in ambiguous_names
                    ):
                        self._record_library_conflict(
                            result,
                            code="DRIVE_NAME_COLLISION",
                            resource_id=drive_folder_id,
                            resource_name=candidate_name,
                            detail=(
                                "同じ場所に同名のGoogle Driveフォルダが複数あるため、"
                                "どのフォルダも自動対応付けしていません。"
                            ),
                            drive_root_id=root_id,
                        )
                        continue
                    mapping = session.scalar(
                        select(DocumentFolderDriveMapping).where(
                            DocumentFolderDriveMapping.google_subject == subject,
                            DocumentFolderDriveMapping.drive_folder_id == drive_folder_id,
                        )
                    )
                    if mapping is not None:
                        if mapping.drive_root_folder_id != root_id:
                            self._record_library_conflict(
                                result,
                                code="DRIVE_ROOT_MISMATCH",
                                resource_id=drive_folder_id,
                                detail="同じDriveフォルダが別の同期ルートに対応付けられています。",
                                mapping_id=mapping.id,
                                drive_root_id=root_id,
                            )
                            continue
                        folder = session.get(DocumentFolder, mapping.document_folder_id)
                        if folder is None:
                            session.delete(mapping)
                            session.flush()
                            mapping = None
                        else:
                            properties = item.get("appProperties")
                            if not isinstance(properties, dict) or (
                                properties.get("llmOcrLibraryFolderId") != folder.id
                                or properties.get("llmOcrDriveRootId") != root_id
                            ):
                                item = self._tag_library_drive_folder(
                                    item,
                                    folder_id=folder.id,
                                    root_id=root_id,
                                )
                            remote_name = str(item.get("name") or "")
                            expected_parent = self._mapped_parent_id(
                                session,
                                subject,
                                folder.parent_id,
                                root_id,
                            )
                            remote_name_changed = remote_name != mapping.drive_name
                            local_name_changed = folder.name != mapping.drive_name
                            remote_parent_changed = (
                                drive_parent_id != mapping.drive_parent_folder_id
                            )
                            local_parent_changed = (
                                expected_parent != mapping.drive_parent_folder_id
                            )
                            name_conflict = (
                                remote_name_changed
                                and local_name_changed
                                and remote_name != folder.name
                            )
                            parent_conflict = (
                                remote_parent_changed
                                and local_parent_changed
                                and drive_parent_id != expected_parent
                            )
                            if name_conflict or parent_conflict:
                                self._record_library_conflict(
                                    result,
                                    code="BOTH_MODIFIED",
                                    resource_id=folder.id,
                                    resource_name=folder.name,
                                    detail=(
                                        "Drive側と独自ライブラリ側のフォルダが"
                                        "どちらも変更されています。"
                                    ),
                                    mapping_id=mapping.id,
                                    drive_root_id=root_id,
                                )
                            else:
                                import_name = remote_name_changed and not local_name_changed
                                import_parent = (
                                    remote_parent_changed and not local_parent_changed
                                )
                                target_name = folder.name
                                target_parent_id = folder.parent_id
                                cleaned: str | None = None
                                if import_name:
                                    cleaned = self._drive_folder_name(remote_name)
                                    target_name = cleaned or remote_name
                                if import_parent:
                                    target_parent_id = local_parent_id

                                validation_error: tuple[str, str] | None = None
                                if import_name and cleaned is None:
                                    validation_error = (
                                        "DRIVE_FOLDER_NAME_INVALID",
                                        "Drive側のフォルダ名を独自ライブラリで使用できません。",
                                    )
                                elif (import_name or import_parent) and self._folder_name_exists(
                                    session,
                                    target_parent_id,
                                    target_name.casefold(),
                                    exclude_id=folder.id,
                                ):
                                    validation_error = (
                                        "DRIVE_NAME_COLLISION",
                                        "移動先に同名フォルダがあるため変更を取り込めません。",
                                    )
                                elif import_parent and self._would_create_folder_cycle(
                                    session,
                                    folder.id,
                                    target_parent_id,
                                ):
                                    validation_error = (
                                        "DOCUMENT_FOLDER_CYCLE",
                                        "循環するフォルダ移動は取り込めません。",
                                    )

                                if validation_error is not None:
                                    self._record_library_conflict(
                                        result,
                                        code=validation_error[0],
                                        resource_id=folder.id,
                                        resource_name=remote_name or folder.name,
                                        detail=validation_error[1],
                                        mapping_id=mapping.id,
                                        drive_root_id=root_id,
                                    )
                                else:
                                    changed = False
                                    if import_name and cleaned is not None:
                                        folder.name = cleaned
                                        folder.normalized_name = cleaned.casefold()
                                        changed = True
                                    if import_parent:
                                        folder.parent_id = target_parent_id
                                        changed = True

                                    expected_after = self._mapped_parent_id(
                                        session,
                                        subject,
                                        folder.parent_id,
                                        root_id,
                                    )
                                    if remote_name == folder.name:
                                        if mapping.drive_name != remote_name:
                                            changed = True
                                        mapping.drive_name = remote_name
                                    if drive_parent_id == expected_after:
                                        if (
                                            mapping.drive_parent_folder_id
                                            != drive_parent_id
                                        ):
                                            changed = True
                                        mapping.drive_parent_folder_id = drive_parent_id
                                    mapping.drive_modified_at = self._drive_datetime(
                                        item.get("modifiedTime")
                                    )
                                    mapping.last_synced_at = datetime.now(UTC)
                                    if changed:
                                        result["foldersUpdated"] += 1
                            queue.append((drive_folder_id, folder.id))
                            session.commit()
                            continue

                    cleaned = self._drive_folder_name(str(item.get("name") or ""))
                    if cleaned is None:
                        self._record_library_conflict(
                            result,
                            code="DRIVE_FOLDER_NAME_INVALID",
                            resource_id=drive_folder_id,
                            resource_name=str(item.get("name") or ""),
                            detail="Drive側のフォルダ名を独自ライブラリで使用できません。",
                            drive_root_id=root_id,
                        )
                        continue
                    properties = item.get("appProperties")
                    managed_folder_id = (
                        properties.get("llmOcrLibraryFolderId")
                        if isinstance(properties, dict)
                        else None
                    )
                    folder = (
                        session.get(DocumentFolder, managed_folder_id)
                        if isinstance(managed_folder_id, str) and managed_folder_id
                        else None
                    )
                    if managed_folder_id and folder is None:
                        self._record_library_conflict(
                            result,
                            code="LOCAL_FOLDER_REMOVED",
                            resource_id=drive_folder_id,
                            resource_name=cleaned,
                            detail=(
                                "独自ライブラリで削除済みのフォルダがDrive側に残っています。"
                                "自動では復元していません。"
                            ),
                            drive_root_id=root_id,
                        )
                        continue
                    if folder is None:
                        parent_condition = (
                            DocumentFolder.parent_id.is_(None)
                            if local_parent_id is None
                            else DocumentFolder.parent_id == local_parent_id
                        )
                        folder = session.scalar(
                            select(DocumentFolder).where(
                                parent_condition,
                                DocumentFolder.normalized_name == cleaned.casefold(),
                            )
                        )
                    if folder is None:
                        folder = DocumentFolder(
                            parent_id=local_parent_id,
                            name=cleaned,
                            normalized_name=cleaned.casefold(),
                        )
                        session.add(folder)
                        session.flush()
                        result["foldersImported"] += 1
                        # Persist the import intent before tagging Drive. If a
                        # later Drive request fails, the stable local id remains
                        # available and the next sync can resume safely.
                        session.commit()
                        folder = session.get(DocumentFolder, folder.id)
                        assert folder is not None
                    existing_root_mapping = session.scalar(
                        select(DocumentFolderDriveMapping).where(
                            DocumentFolderDriveMapping.google_subject == subject,
                            DocumentFolderDriveMapping.document_folder_id == folder.id,
                            DocumentFolderDriveMapping.drive_root_folder_id == root_id,
                        )
                    )
                    if existing_root_mapping is not None:
                        previous_item = self._drive_item(
                            existing_root_mapping.drive_folder_id
                        )
                        if (
                            previous_item is not None
                            and previous_item.get("trashed") is True
                            and managed_folder_id is None
                        ):
                            existing_root_mapping.drive_folder_id = drive_folder_id
                            existing_root_mapping.drive_parent_folder_id = drive_parent_id
                            self._update_mapping_snapshot(existing_root_mapping, item)
                            session.commit()
                            item = self._tag_library_drive_folder(
                                item,
                                folder_id=folder.id,
                                root_id=root_id,
                            )
                            existing_root_mapping = session.get(
                                DocumentFolderDriveMapping,
                                existing_root_mapping.id,
                            )
                            assert existing_root_mapping is not None
                            self._update_mapping_snapshot(existing_root_mapping, item)
                            session.commit()
                            queue.append((drive_folder_id, folder.id))
                            continue
                        self._record_library_conflict(
                            result,
                            code="DRIVE_NAME_COLLISION",
                            resource_id=drive_folder_id,
                            detail="Drive側の同名フォルダを一意に対応付けできません。",
                            drive_root_id=root_id,
                        )
                        continue
                    mapping = DocumentFolderDriveMapping(
                        google_subject=subject,
                        document_folder_id=folder.id,
                        drive_root_folder_id=root_id,
                        drive_folder_id=drive_folder_id,
                        drive_name=cleaned,
                        drive_parent_folder_id=drive_parent_id,
                    )
                    self._update_mapping_snapshot(mapping, item)
                    session.add(mapping)
                    # Persist the remote/local identity before tagging Drive.
                    # A lost PATCH response can then resume with the same local
                    # folder even if the local name or parent changes meanwhile.
                    session.commit()
                    item = self._tag_library_drive_folder(
                        item,
                        folder_id=folder.id,
                        root_id=root_id,
                    )
                    if isinstance(item_cache, dict):
                        item_cache[drive_folder_id] = item
                    mapping = session.get(DocumentFolderDriveMapping, mapping.id)
                    assert mapping is not None
                    self._update_mapping_snapshot(mapping, item)
                    session.commit()
                    queue.append((drive_folder_id, folder.id))
            session.commit()

    def _sync_drive_documents(
        self,
        subject: str,
        roots: list[tuple[str, str | None]],
        result: dict[str, Any],
    ) -> None:
        root_names = {folder_id: name for folder_id, name in roots}
        root_ids = set(root_names)
        recreated_value = result.get("_recreatedFolderParents")
        recreated_parents = (
            recreated_value
            if isinstance(recreated_value, dict)
            else {}
        )
        recreated_targets_value = result.get("_recreatedFolderTargets")
        recreated_targets = (
            recreated_targets_value
            if isinstance(recreated_targets_value, dict)
            else {}
        )
        with self._database.session() as session:
            documents = list(
                session.scalars(
                    select(LedgerDocument).where(
                        LedgerDocument.spreadsheet_id.is_not(None),
                        LedgerDocument.library_deleted_at.is_(None),
                    )
                )
            )
            for ledger in documents:
                if ledger.spreadsheet_id is None:
                    continue
                document_label = (
                    ledger.document_name
                    or ledger.library_name
                    or ledger.individual_id
                )
                if (
                    ledger.drive_google_subject is not None
                    and ledger.drive_google_subject != subject
                ):
                    # Documents owned by another connected account are outside
                    # this account's sync set, not a conflict in that set.
                    continue
                if ledger.drive_google_subject is None:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_DOCUMENT_OWNER_UNCONFIRMED",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail=(
                            "所有アカウントが確定していない旧書類は、"
                            "誤ったアカウントへ移管しないため自動同期していません。"
                        ),
                        drive_root_id=ledger.drive_root_folder_id,
                    )
                    continue
                root_id = (
                    ledger.drive_root_folder_id
                    or self._effective_root_for_document(session, subject, ledger)
                    if ledger.drive_google_subject == subject
                    else None
                )
                item_cache = result.get("_driveItemCache")
                cached_item = (
                    item_cache.get(ledger.spreadsheet_id)
                    if isinstance(item_cache, dict)
                    else None
                )
                item = (
                    cached_item
                    if isinstance(cached_item, dict)
                    else self._drive_item(ledger.spreadsheet_id)
                )
                if item is None:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_SPREADSHEET_REMOVED",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail="Drive側のスプレッドシートが見つかりません。",
                        drive_root_id=root_id,
                    )
                    continue
                parents = self._string_list(item.get("parents"))
                if ledger.drive_google_subject is None:
                    matched_root = next(
                        (parent_id for parent_id in parents if parent_id in root_ids),
                        None,
                    )
                    if matched_root is None and len(parents) == 1:
                        claim_mapping = session.scalar(
                            select(DocumentFolderDriveMapping).where(
                                DocumentFolderDriveMapping.google_subject == subject,
                                DocumentFolderDriveMapping.drive_folder_id == parents[0],
                                DocumentFolderDriveMapping.drive_root_folder_id.in_(root_ids),
                            )
                        )
                        if claim_mapping is not None:
                            matched_root = claim_mapping.drive_root_folder_id
                    if matched_root is not None:
                        root_id = matched_root
                if not root_id:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_ROOT_UNKNOWN",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail="スプレッドシートの同期ルートを確認できません。",
                    )
                    continue
                if ledger.drive_google_subject is None and root_id not in root_ids:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_DOCUMENT_ACCOUNT_MISMATCH",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail=(
                            "書類が現在接続中のGoogle Drive保存先に属していないため、"
                            "変更していません。"
                        ),
                        drive_root_id=root_id,
                    )
                    continue
                if (
                    ledger.drive_google_subject is None
                    and self._configured_root_subjects(
                        session,
                        root_id,
                        ledger.template_id,
                    )
                    != {subject}
                ):
                    self._record_library_conflict(
                        result,
                        code="DRIVE_DOCUMENT_ACCOUNT_AMBIGUOUS",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail=(
                            "同じ保存先が複数のGoogleアカウントに設定されているため、"
                            "書類の所有アカウントを確定できません。"
                        ),
                        drive_root_id=root_id,
                    )
                    continue
                repaired_parent = None
                if ledger.library_folder_id is not None:
                    candidate = recreated_targets.get(
                        f"{root_id}:{ledger.library_folder_id}"
                    )
                    if isinstance(candidate, str) and candidate:
                        repaired_parent = candidate
                    elif (
                        item.get("trashed") is True
                        and item.get("explicitlyTrashed") is not True
                    ):
                        current_mapping = session.scalar(
                            select(DocumentFolderDriveMapping).where(
                                DocumentFolderDriveMapping.google_subject == subject,
                                DocumentFolderDriveMapping.document_folder_id
                                == ledger.library_folder_id,
                                DocumentFolderDriveMapping.drive_root_folder_id == root_id,
                            )
                        )
                        if current_mapping is not None:
                            repaired_parent = current_mapping.drive_folder_id
                if repaired_parent is None and len(parents) == 1:
                    candidate = recreated_parents.get(parents[0])
                    if isinstance(candidate, str) and candidate:
                        repaired_parent = candidate
                repaired = False
                if repaired_parent is not None and item.get("explicitlyTrashed") is not True:
                    latest_item = self._drive_item(ledger.spreadsheet_id)
                    if latest_item is None:
                        self._record_library_conflict(
                            result,
                            code="DRIVE_SPREADSHEET_REMOVED",
                            resource_id=ledger.id,
                            resource_name=document_label,
                            detail="修復直前にDrive側のスプレッドシートが見つからなくなりました。",
                            drive_root_id=root_id,
                        )
                        continue
                    item = latest_item
                    parents = self._string_list(item.get("parents"))
                    if item.get("trashed") is True:
                        item = self._request(
                            "PATCH",
                            f"{_DRIVE_FILES}/{quote(ledger.spreadsheet_id, safe='')}",
                            params={
                                "fields": (
                                    "id,name,mimeType,parents,modifiedTime,trashed,"
                                    "explicitlyTrashed,appProperties"
                                ),
                                "supportsAllDrives": "true",
                            },
                            json={"trashed": False},
                        )
                        parents = self._string_list(item.get("parents"))
                    if parents != [repaired_parent]:
                        item = self._move_drive_item(
                            ledger.spreadsheet_id,
                            repaired_parent,
                            parents,
                        )
                    parents = self._string_list(item.get("parents"))
                    repaired = parents == [repaired_parent] and item.get("trashed") is not True
                if item.get("trashed") is True:
                    self._record_library_conflict(
                        result,
                        code=(
                            "DRIVE_SPREADSHEET_EXPLICITLY_TRASHED"
                            if item.get("explicitlyTrashed") is True
                            else "DRIVE_SPREADSHEET_REMOVED"
                        ),
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail="Drive側のスプレッドシートがゴミ箱にあります。",
                    )
                    continue
                if len(parents) != 1:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_PARENT_UNKNOWN",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail="スプレッドシートの保存場所を一意に確認できません。",
                    )
                    continue
                drive_parent_id = parents[0]
                target_folder_id: str | None = None
                observed_mapping = session.scalar(
                    select(DocumentFolderDriveMapping).where(
                        DocumentFolderDriveMapping.google_subject == subject,
                        DocumentFolderDriveMapping.drive_folder_id == drive_parent_id,
                        *(
                            (
                                DocumentFolderDriveMapping.drive_root_folder_id.in_(
                                    root_ids
                                ),
                            )
                            if ledger.drive_google_subject is None
                            else ()
                        ),
                    )
                )
                if drive_parent_id in root_ids:
                    root_id = drive_parent_id
                elif observed_mapping is not None:
                    root_id = observed_mapping.drive_root_folder_id
                    target_folder_id = observed_mapping.document_folder_id
                elif drive_parent_id != root_id:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_DOCUMENT_OUTSIDE_LIBRARY",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail=(
                            "スプレッドシートが同期対象外のDriveフォルダへ"
                            "移動されています。"
                        ),
                        drive_root_id=root_id,
                    )
                    continue

                changed = repaired
                drive_name = self._drive_document_name(str(item.get("name") or ""))
                local_name = ledger.document_name or ledger.library_name
                pending_output = session.scalar(
                    select(SpreadsheetOutputOperation)
                    .where(
                        SpreadsheetOutputOperation.document_id == ledger.id,
                        SpreadsheetOutputOperation.google_subject == subject,
                        SpreadsheetOutputOperation.source_job_id
                        == ledger.current_job_id,
                    )
                    .order_by(
                        SpreadsheetOutputOperation.source_revision.desc(),
                        SpreadsheetOutputOperation.updated_at.desc(),
                    )
                    .limit(1)
                )
                pending_local_rename = bool(
                    pending_output is not None
                    and local_name
                    and drive_name != local_name
                    and (
                        pending_output.spreadsheet_id != ledger.spreadsheet_id
                        or pending_output.drive_name != local_name
                    )
                )
                if drive_name is None:
                    self._record_library_conflict(
                        result,
                        code="DRIVE_DOCUMENT_NAME_INVALID",
                        resource_id=ledger.id,
                        resource_name=document_label,
                        detail="Drive側の書類名を独自ライブラリで使用できません。",
                        drive_root_id=root_id,
                    )
                elif pending_local_rename:
                    self._record_library_conflict(
                        result,
                        code="SPREADSHEET_OUTPUT_PENDING",
                        resource_id=ledger.id,
                        resource_name=local_name,
                        detail=(
                            "Google Driveへの名前反映が完了していないため、"
                            "Drive側の旧名では独自ライブラリを上書きしていません。"
                        ),
                        drive_root_id=root_id,
                    )
                elif drive_name != local_name:
                    ledger.document_name = drive_name
                    ledger.library_name = drive_name
                    session.execute(
                        update(ConfirmedResult)
                        .where(ConfirmedResult.spreadsheet_id == ledger.spreadsheet_id)
                        .values(spreadsheet_name=drive_name)
                    )
                    changed = True
                if ledger.library_folder_id != target_folder_id:
                    ledger.library_folder_id = target_folder_id
                    ledger.library_location_version += 1
                    changed = True
                folder_path = self._library_folder_path(session, target_folder_id)
                root_path = root_names.get(root_id)
                drive_path = ledger.drive_folder_path
                if root_id in root_names:
                    drive_path = (
                        f"{root_path}/{folder_path}"
                        if root_path and folder_path
                        else folder_path or root_path
                    )
                if (
                    ledger.drive_google_subject != subject
                    or ledger.drive_root_folder_id != root_id
                    or ledger.drive_folder_id != drive_parent_id
                    or ledger.drive_folder_path != drive_path
                ):
                    ledger.drive_google_subject = subject
                    ledger.drive_root_folder_id = root_id
                    ledger.drive_folder_id = drive_parent_id
                    ledger.drive_folder_path = drive_path
                    changed = True
                if changed:
                    result["documentsUpdated"] += 1
            session.commit()

    def _write_library_sync_state(
        self,
        subject: str,
        result: dict[str, Any],
        *,
        status: str,
    ) -> None:
        with self._database.session() as session:
            state = session.get(DocumentLibrarySyncState, subject)
            if state is None:
                state = DocumentLibrarySyncState(google_subject=subject)
                session.add(state)
            state.status = status
            attempt = result.get("lastAttemptAt")
            state.last_attempt_at = attempt if isinstance(attempt, datetime) else datetime.now(UTC)
            success = result.get("lastSuccessAt")
            if isinstance(success, datetime):
                state.last_success_at = success
            error = result.get("error")
            state.error = str(error) if error else None
            details = result.get("conflictItems")
            state.conflicts_json = details if isinstance(details, list) else []
            if "_driveChangeToken" in result:
                token = result.get("_driveChangeToken")
                state.drive_change_token = token if isinstance(token, str) else None
            roots = result.get("_driveRoots")
            if isinstance(roots, list):
                state.drive_roots_json = [
                    str(root_id) for root_id in roots if root_id
                ]
            session.commit()

    def _start_drive_change_token(self) -> str:
        payload = self._request(
            "GET",
            f"{_DRIVE_CHANGES}/startPageToken",
            params={"supportsAllDrives": "true"},
        )
        token = payload.get("startPageToken")
        if not isinstance(token, str) or not token:
            raise AppError(
                status=502,
                code="DRIVE_CHANGE_TOKEN_MISSING",
                title="Drive change token was not returned",
                detail="Google Driveの差分同期開始位置を取得できませんでした。",
            )
        return token

    def _list_drive_changes(
        self,
        page_token: str,
    ) -> tuple[list[dict[str, Any]], str]:
        changes: list[dict[str, Any]] = []
        current_token = page_token
        while True:
            payload = self._request(
                "GET",
                _DRIVE_CHANGES,
                params={
                    "pageToken": current_token,
                    "pageSize": "1000",
                    "includeRemoved": "true",
                    "includeItemsFromAllDrives": "true",
                    "supportsAllDrives": "true",
                    "fields": (
                        "nextPageToken,newStartPageToken,changes("
                        "changeType,fileId,removed,file("
                        "id,name,mimeType,parents,modifiedTime,trashed,"
                        "explicitlyTrashed,appProperties))"
                    ),
                },
            )
            page_changes = payload.get("changes")
            if isinstance(page_changes, list):
                changes.extend(
                    change for change in page_changes if isinstance(change, dict)
                )
            next_token = payload.get("nextPageToken")
            if isinstance(next_token, str) and next_token:
                current_token = next_token
                continue
            new_token = payload.get("newStartPageToken")
            return changes, (
                new_token
                if isinstance(new_token, str) and new_token
                else current_token
            )

    def _changes_affect_library(
        self,
        subject: str,
        root_ids: set[str],
        changes: list[dict[str, Any]],
    ) -> bool:
        with self._database.session() as session:
            known_ids = set(root_ids)
            known_ids.update(
                str(folder_id)
                for folder_id in session.scalars(
                    select(DocumentFolderDriveMapping.drive_folder_id).where(
                        DocumentFolderDriveMapping.google_subject == subject,
                        DocumentFolderDriveMapping.drive_root_folder_id.in_(root_ids),
                    )
                )
            )
            known_ids.update(
                str(spreadsheet_id)
                for spreadsheet_id in session.scalars(
                    select(LedgerDocument.spreadsheet_id).where(
                        LedgerDocument.drive_google_subject == subject,
                        LedgerDocument.drive_root_folder_id.in_(root_ids),
                        LedgerDocument.spreadsheet_id.is_not(None),
                    )
                )
                if spreadsheet_id
            )
        relevant_parents = set(known_ids)
        for change in changes:
            if change.get("changeType") != "file":
                continue
            file_id = change.get("fileId")
            if isinstance(file_id, str) and file_id in known_ids:
                return True
            item = change.get("file")
            if not isinstance(item, dict):
                continue
            parents = set(self._string_list(item.get("parents")))
            properties = item.get("appProperties")
            tagged_root = (
                properties.get("llmOcrDriveRootId")
                if isinstance(properties, dict)
                else None
            )
            if parents & relevant_parents or tagged_root in root_ids:
                candidate_id = item.get("id")
                if isinstance(candidate_id, str) and candidate_id:
                    relevant_parents.add(candidate_id)
                return True
        return False

    def _has_local_library_drift(
        self,
        subject: str,
        roots: list[tuple[str, str | None]],
        last_success_at: datetime | None,
    ) -> bool:
        if last_success_at is None:
            return True
        root_ids = {root_id for root_id, _name in roots}
        with self._database.session() as session:
            if session.scalar(
                select(LedgerDocument.id)
                .where(
                    LedgerDocument.spreadsheet_id.is_not(None),
                    LedgerDocument.drive_google_subject.is_(None),
                    LedgerDocument.library_deleted_at.is_(None),
                )
                .limit(1)
            ) is not None:
                # A legacy spreadsheet binding may have been restored from the
                # confirmed result without changing its old timestamp. Force a
                # full pass so the UI exposes the required ownership decision.
                return True
            if session.scalar(
                select(DocumentFolder.id)
                .where(DocumentFolder.updated_at > last_success_at)
                .limit(1)
            ) is not None:
                return True
            if session.scalar(
                select(LedgerDocument.id)
                .where(
                    LedgerDocument.updated_at > last_success_at,
                    LedgerDocument.drive_google_subject == subject,
                )
                .limit(1)
            ) is not None:
                return True
            folders = list(session.scalars(select(DocumentFolder)))
            mappings = list(
                session.scalars(
                    select(DocumentFolderDriveMapping).where(
                        DocumentFolderDriveMapping.google_subject == subject,
                        DocumentFolderDriveMapping.drive_root_folder_id.in_(root_ids),
                    )
                )
            )
            by_local_root = {
                (mapping.document_folder_id, mapping.drive_root_folder_id): mapping
                for mapping in mappings
            }
            for root_id in root_ids:
                for folder in folders:
                    mapping = by_local_root.get((folder.id, root_id))
                    expected_parent = self._mapped_parent_id(
                        session,
                        subject,
                        folder.parent_id,
                        root_id,
                    )
                    if (
                        mapping is None
                        or mapping.drive_name != folder.name
                        or mapping.drive_parent_folder_id != expected_parent
                    ):
                        return True
        return False

    def _effective_root_for_document(
        self,
        session: Session,
        subject: str,
        ledger: LedgerDocument,
    ) -> str | None:
        selected = session.get(
            TemplateOutputFolder,
            (subject, ledger.template_id),
        )
        if selected is not None:
            return selected.folder_id
        account = session.get(GoogleOAuthAccount, subject)
        return account.default_output_folder_id if account is not None else None

    def _assert_spreadsheet_in_current_roots(self, spreadsheet_id: str) -> None:
        subject = self._require_subject()
        with self._database.session() as session:
            ledger = session.scalar(
                select(LedgerDocument).where(
                    LedgerDocument.spreadsheet_id == spreadsheet_id
                )
            )
            if ledger is None:
                return
            self._assert_ledger_spreadsheet_in_current_roots(session, ledger, subject)
            session.commit()

    def _assert_ledger_spreadsheet_in_current_roots(
        self,
        session: Session,
        ledger: LedgerDocument,
        subject: str,
    ) -> None:
        if not ledger.spreadsheet_id:
            return
        if ledger.drive_google_subject is not None:
            if ledger.drive_google_subject != subject:
                raise self._drive_document_account_mismatch()
            return
        raise self._drive_document_account_ambiguous()

    @staticmethod
    def _drive_document_account_mismatch() -> AppError:
        return AppError(
            status=409,
            code="DRIVE_DOCUMENT_ACCOUNT_MISMATCH",
            title="Drive document belongs to another Google account",
            detail=(
                "この書類は現在接続中のGoogleアカウントに属していません。"
                "元のGoogleアカウントへ接続して操作してください。"
            ),
        )

    @staticmethod
    def _drive_document_account_ambiguous() -> AppError:
        return AppError(
            status=409,
            code="DRIVE_DOCUMENT_ACCOUNT_AMBIGUOUS",
            title="Drive document account is ambiguous",
            detail=(
                "所有アカウントが確定していない旧書類は自動では関連付けません。"
                "管理者が所有アカウントを明示的に確認してから操作してください。"
            ),
        )

    @staticmethod
    def _configured_root_subjects(
        session: Session,
        root_id: str,
        template_id: str,
    ) -> set[str]:
        default_subjects = session.scalars(
            select(GoogleOAuthAccount.google_subject).where(
                GoogleOAuthAccount.default_output_folder_id == root_id
            )
        )
        template_subjects = session.scalars(
            select(TemplateOutputFolder.google_subject).where(
                TemplateOutputFolder.folder_id == root_id,
                TemplateOutputFolder.template_id == template_id,
            )
        )
        return {
            str(candidate)
            for candidate in (*default_subjects, *template_subjects)
        }

    @staticmethod
    def _drive_folder_mapping_ambiguous(folder_name: str) -> AppError:
        return AppError(
            status=409,
            code="DRIVE_FOLDER_MAPPING_AMBIGUOUS",
            title="Drive folder mapping is ambiguous",
            detail=(
                f"Google Drive側に対応先を一意に決められない「{folder_name}」があります。"
                "同名フォルダを整理してから再同期してください。"
            ),
        )

    def _mapped_parent_id(
        self,
        session: Session,
        subject: str,
        local_parent_id: str | None,
        root_id: str,
    ) -> str:
        if local_parent_id is None:
            return root_id
        mapping = session.scalar(
            select(DocumentFolderDriveMapping).where(
                DocumentFolderDriveMapping.google_subject == subject,
                DocumentFolderDriveMapping.document_folder_id == local_parent_id,
                DocumentFolderDriveMapping.drive_root_folder_id == root_id,
            )
        )
        return mapping.drive_folder_id if mapping is not None else ""

    def _create_library_drive_folder(
        self,
        folder: DocumentFolder,
        *,
        root_id: str,
        parent_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            _DRIVE_FILES,
            params={
                "fields": (
                    "id,name,mimeType,parents,modifiedTime,trashed,"
                    "explicitlyTrashed,appProperties"
                ),
                "supportsAllDrives": "true",
            },
            json={
                "name": folder.name,
                "mimeType": _FOLDER_MIME,
                "parents": [parent_id],
                "appProperties": {
                    "llmOcrLibraryFolderId": folder.id,
                    "llmOcrDriveRootId": root_id,
                },
            },
        )

    def _tag_library_drive_folder(
        self,
        item: dict[str, Any],
        *,
        folder_id: str,
        root_id: str,
    ) -> dict[str, Any]:
        properties = item.get("appProperties")
        current = properties if isinstance(properties, dict) else {}
        if (
            current.get("llmOcrLibraryFolderId") == folder_id
            and current.get("llmOcrDriveRootId") == root_id
        ):
            return item
        return self._request(
            "PATCH",
            f"{_DRIVE_FILES}/{quote(str(item['id']), safe='')}",
            params={
                "fields": (
                    "id,name,mimeType,parents,modifiedTime,trashed,"
                    "explicitlyTrashed,appProperties"
                ),
                "supportsAllDrives": "true",
            },
            json={
                "appProperties": {
                    "llmOcrLibraryFolderId": folder_id,
                    "llmOcrDriveRootId": root_id,
                }
            },
        )

    def _drive_item(self, file_id: str) -> dict[str, Any] | None:
        item = self._request(
            "GET",
            f"{_DRIVE_FILES}/{quote(file_id, safe='')}",
            params={
                "fields": (
                    "id,name,mimeType,parents,createdTime,modifiedTime,size,trashed,"
                    "explicitlyTrashed,appProperties"
                ),
                "supportsAllDrives": "true",
            },
            allow_not_found=True,
        )
        return item if item.get("id") else None

    def _list_drive_children(
        self,
        parent_id: str,
        *,
        include_trashed: bool = False,
    ) -> list[dict[str, Any]]:
        escaped_parent = parent_id.replace("\\", "\\\\").replace("'", "\\'")
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {
                "q": (
                    f"'{escaped_parent}' in parents"
                    if include_trashed
                    else f"'{escaped_parent}' in parents and trashed = false"
                ),
                "fields": (
                    "nextPageToken,files("
                    "id,name,mimeType,parents,createdTime,modifiedTime,size,trashed,"
                    "explicitlyTrashed,appProperties)"
                ),
                "pageSize": "1000",
                "spaces": "drive",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._request("GET", _DRIVE_FILES, params=params)
            page_items = payload.get("files")
            if isinstance(page_items, list):
                items.extend(item for item in page_items if isinstance(item, dict))
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return items
            page_token = next_token

    def _template_package_root(self, subject: str) -> dict[str, Any]:
        with self._database.session() as session:
            account = self._active_account(session, subject)
            parent_id = account.default_output_folder_id
        if not parent_id:
            raise AppError(
                status=409,
                code="DRIVE_OUTPUT_FOLDER_REQUIRED",
                title="Drive output folder is required",
                detail="先に保存先設定でGoogle Driveフォルダーを選択してください。",
            )
        candidates = [
            item
            for item in self._list_drive_children(parent_id)
            if item.get("mimeType") == _FOLDER_MIME
            and self._template_properties(item).get("llmOcrTemplateRoot")
            == _TEMPLATE_ROOT_MARKER
        ]
        if candidates:
            return sorted(candidates, key=lambda item: str(item.get("id") or ""))[0]
        created = self._create_file(
            name=_TEMPLATE_ROOT_NAME,
            mime_type=_FOLDER_MIME,
            parent_id=parent_id,
            app_properties={"llmOcrTemplateRoot": _TEMPLATE_ROOT_MARKER},
        )
        verified = self._drive_item(str(created.get("id") or ""))
        if (
            verified is None
            or verified.get("mimeType") != _FOLDER_MIME
            or self._template_properties(verified).get("llmOcrTemplateRoot")
            != _TEMPLATE_ROOT_MARKER
        ):
            raise AppError(
                status=409,
                code="DRIVE_TEMPLATE_ROOT_VERIFICATION_FAILED",
                title="Drive template root verification failed",
                detail="Google Driveのtemplate package専用フォルダーを確認できませんでした。",
            )
        return verified

    def _template_package_identity_folders(
        self,
        *,
        root_id: str,
        publisher_id: str,
        package_id: str,
    ) -> dict[str, str]:
        publisher = self._ensure_template_folder(
            parent_id=root_id,
            name=publisher_id,
            marker_key="llmOcrTemplatePublisher",
            marker_value=self._identity_hash(publisher_id),
        )
        package = self._ensure_template_folder(
            parent_id=str(publisher["id"]),
            name=package_id,
            marker_key="llmOcrTemplatePackage",
            marker_value=self._identity_hash(package_id),
        )
        releases = self._ensure_template_folder(
            parent_id=str(package["id"]),
            name="releases",
            marker_key="llmOcrTemplateCollection",
            marker_value="releases",
        )
        drafts = self._ensure_template_folder(
            parent_id=str(package["id"]),
            name="drafts",
            marker_key="llmOcrTemplateCollection",
            marker_value="drafts",
        )
        return {
            "publisher": str(publisher["id"]),
            "package": str(package["id"]),
            "releases": str(releases["id"]),
            "drafts": str(drafts["id"]),
        }

    def _ensure_template_folder(
        self,
        *,
        parent_id: str,
        name: str,
        marker_key: str,
        marker_value: str,
    ) -> dict[str, Any]:
        matches = [
            item
            for item in self._list_drive_children(parent_id)
            if item.get("mimeType") == _FOLDER_MIME
            and self._template_properties(item).get(marker_key) == marker_value
        ]
        if matches:
            if len(matches) != 1:
                raise AppError(
                    status=409,
                    code="DRIVE_TEMPLATE_FOLDER_CONFLICT",
                    title="Drive template folder conflict",
                    detail="Google Drive側に対応先を一意に決められないフォルダーがあります。",
                )
            return matches[0]
        created = self._create_file(
            name=name[:255],
            mime_type=_FOLDER_MIME,
            parent_id=parent_id,
            app_properties={marker_key: marker_value},
        )
        verified = self._drive_item(str(created.get("id") or ""))
        if (
            verified is None
            or verified.get("mimeType") != _FOLDER_MIME
            or self._template_properties(verified).get(marker_key) != marker_value
            or self._string_list(verified.get("parents")) != [parent_id]
        ):
            raise AppError(
                status=409,
                code="DRIVE_TEMPLATE_FOLDER_VERIFICATION_FAILED",
                title="Drive template folder verification failed",
                detail="Google Driveのtemplate package保存先を確認できませんでした。",
            )
        return verified

    def _find_template_package_files(self, root_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {
                "q": (
                    "appProperties has { key='lot' and value='v1' } "
                    "and trashed = false"
                ),
                "fields": f"nextPageToken,files({_TEMPLATE_FILE_FIELDS})",
                "pageSize": "1000",
                "spaces": "drive",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._request("GET", _DRIVE_FILES, params=params)
            page_items = payload.get("files")
            if isinstance(page_items, list):
                for item in page_items:
                    if not isinstance(item, dict):
                        continue
                    if self._template_properties(item).get("loroot") != root_id:
                        continue
                    detailed = self._drive_item_with_etag(str(item.get("id") or ""))
                    if detailed:
                        items.append(detailed)
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return items
            page_token = next_token

    def _drive_item_with_etag(self, file_id: str) -> dict[str, Any]:
        response = self._raw_drive_request(
            "GET",
            f"{_DRIVE_FILES}/{quote(file_id, safe='')}",
            params={"fields": _TEMPLATE_FILE_FIELDS, "supportsAllDrives": "true"},
        )
        payload = response.json()
        item = payload if isinstance(payload, dict) else {}
        etag = response.headers.get("ETag")
        if etag:
            item["_etag"] = etag
        return item

    def _upload_template_package(
        self,
        *,
        method: str,
        file_id: str | None,
        content: bytes,
        metadata: dict[str, Any],
        etag: str | None,
    ) -> dict[str, Any]:
        boundary = f"llm_ocr_template_{uuid4().hex}"
        metadata_bytes = json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        body = b"".join(
            (
                f"--{boundary}\r\n".encode("ascii"),
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                metadata_bytes,
                f"\r\n--{boundary}\r\n".encode("ascii"),
                f"Content-Type: {_TEMPLATE_PACKAGE_MIME}\r\n\r\n".encode("ascii"),
                content,
                f"\r\n--{boundary}--\r\n".encode("ascii"),
            )
        )
        url = _DRIVE_UPLOAD_FILES
        if file_id is not None:
            url = f"{_DRIVE_UPLOAD_FILES}/{quote(file_id, safe='')}"
        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}
        if etag:
            headers["If-Match"] = etag
        response = self._raw_drive_request(
            method,
            url,
            params={
                "uploadType": "multipart",
                "fields": _TEMPLATE_FILE_FIELDS,
                "supportsAllDrives": "true",
            },
            headers=headers,
            data=body,
            timeout=600,
        )
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _require_matching_draft(
        self,
        item: dict[str, Any],
        *,
        root_id: str,
        draft_id: str,
        expected_revision: int | None,
        expected_drive_version: str | None,
        expected_head_revision_id: str | None,
        expected_etag: str | None,
    ) -> None:
        properties = self._template_properties(item)
        if (
            not self._is_template_package_file(item, root_id=root_id, kind="draft")
            or properties.get("lou") != draft_id
            or properties.get("lor") != str(expected_revision)
            or str(item.get("version") or "") != str(expected_drive_version or "")
            or str(item.get("headRevisionId") or "")
            != str(expected_head_revision_id or "")
            or str(item.get("_etag") or "") != str(expected_etag or "")
        ):
            raise self._template_draft_conflict()

    @staticmethod
    def _template_file_properties(
        *,
        kind: str,
        root_id: str,
        folder_id: str,
        publisher_id: str,
        package_id: str,
        release_version: str,
        content_digest: str,
        package_sha256: str,
        draft_id: str | None = None,
        draft_revision: int | None = None,
        base_release_version: str | None = None,
    ) -> dict[str, str]:
        properties = {
            "lot": _TEMPLATE_ROOT_MARKER,
            "lok": kind,
            "loroot": root_id,
            "lof": folder_id,
            "lop": GoogleDriveService._identity_hash(publisher_id),
            "loi": GoogleDriveService._identity_hash(package_id),
            "lov": release_version,
            "lod": content_digest,
            "los": package_sha256,
        }
        if draft_id is not None and draft_revision is not None:
            properties["lou"] = draft_id
            properties["lor"] = str(draft_revision)
            properties["lob"] = base_release_version or ""
        for key, value in properties.items():
            if len((key + value).encode("utf-8")) > 124:
                raise AppError(
                    status=422,
                    code="DRIVE_TEMPLATE_PROPERTY_TOO_LONG",
                    title="Drive template property is too long",
                    detail="Google Drive同期用metadataの上限を超えています。",
                )
        return properties

    @staticmethod
    def _template_properties(item: dict[str, Any]) -> dict[str, str]:
        value = item.get("appProperties")
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(item_value)
            for key, item_value in value.items()
            if isinstance(key, str) and item_value is not None
        }

    def _is_template_package_file(
        self,
        item: dict[str, Any] | None,
        *,
        root_id: str,
        kind: str | None = None,
    ) -> bool:
        if not item:
            return False
        properties = self._template_properties(item)
        file_kind = properties.get("lok")
        folder_id = properties.get("lof")
        return (
            item.get("mimeType") == _TEMPLATE_PACKAGE_MIME
            and item.get("trashed") is not True
            and properties.get("lot") == _TEMPLATE_ROOT_MARKER
            and properties.get("loroot") == root_id
            and file_kind in {"release", "draft"}
            and (kind is None or file_kind == kind)
            and bool(folder_id)
            and self._string_list(item.get("parents")) == [folder_id]
            and bool(properties.get("lod"))
            and bool(properties.get("los"))
        )

    def _template_package_file_view(self, item: dict[str, Any]) -> dict[str, Any]:
        properties = self._template_properties(item)
        return {
            "kind": properties.get("lok") or "",
            "fileId": str(item.get("id") or ""),
            "fileName": str(item.get("name") or ""),
            "folderId": properties.get("lof") or "",
            "releaseVersion": properties.get("lov") or "",
            "contentDigest": properties.get("lod") or "",
            "packageSha256": properties.get("los") or "",
            "publisherHash": properties.get("lop") or "",
            "packageHash": properties.get("loi") or "",
            "draftId": properties.get("lou") or None,
            "draftRevision": int(properties.get("lor") or 0) or None,
            "baseReleaseVersion": properties.get("lob") or None,
            "driveVersion": str(item.get("version") or ""),
            "headRevisionId": str(item.get("headRevisionId") or "") or None,
            "etag": str(item.get("_etag") or "") or None,
            "modifiedTime": item.get("modifiedTime"),
            "fileBytes": int(item.get("size") or 0),
        }

    @staticmethod
    def _identity_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _template_release_conflict(detail: str) -> AppError:
        return AppError(
            status=409,
            code="DRIVE_TEMPLATE_RELEASE_CONFLICT",
            title="Drive template release conflict",
            detail=detail,
        )

    @staticmethod
    def _template_draft_conflict() -> AppError:
        return AppError(
            status=409,
            code="DRIVE_TEMPLATE_DRAFT_CONFLICT",
            title="Drive template draft conflict",
            detail="別の環境で作業版が更新されています。再取得して内容を確認してください。",
        )

    def _workspace_root(self, subject: str) -> dict[str, Any]:
        with self._database.session() as session:
            account = self._active_account(session, subject)
            parent_id = account.default_output_folder_id
        if not parent_id:
            raise AppError(
                status=409,
                code="DRIVE_OUTPUT_FOLDER_REQUIRED",
                title="Drive output folder is required",
                detail="先に保存先設定でGoogle Driveフォルダーを選択してください。",
            )
        candidates = [
            item
            for item in self._list_drive_children(parent_id)
            if item.get("mimeType") == _FOLDER_MIME
            and self._workspace_properties(item).get("llmOcrWorkspaceRoot")
            == _WORKSPACE_ROOT_MARKER
        ]
        if candidates:
            return sorted(candidates, key=lambda item: str(item.get("id") or ""))[0]
        created = self._create_file(
            name=_WORKSPACE_ROOT_NAME,
            mime_type=_FOLDER_MIME,
            parent_id=parent_id,
            app_properties={"llmOcrWorkspaceRoot": _WORKSPACE_ROOT_MARKER},
        )
        verified = self._drive_item(str(created.get("id") or ""))
        if (
            verified is None
            or verified.get("mimeType") != _FOLDER_MIME
            or self._workspace_properties(verified).get("llmOcrWorkspaceRoot")
            != _WORKSPACE_ROOT_MARKER
        ):
            raise AppError(
                status=409,
                code="WORKSPACE_ROOT_VERIFICATION_FAILED",
                title="Workspace sync folder could not be verified",
                detail="Google Driveの同期用フォルダーを確認できませんでした。",
            )
        return verified

    def _workspace_snapshots(self, root: dict[str, Any]) -> list[dict[str, Any]]:
        root_id = str(root["id"])
        snapshots = [
            self._workspace_snapshot_view(item)
            for item in self._list_drive_children(root_id)
            if self._is_workspace_snapshot(item, root_id=root_id)
        ]
        snapshots.sort(
            key=lambda item: (
                int(item.get("revision") or 0),
                str(item.get("createdAt") or ""),
                str(item.get("fileId") or ""),
            ),
            reverse=True,
        )
        return snapshots

    @staticmethod
    def _workspace_properties(item: dict[str, Any]) -> dict[str, str]:
        value = item.get("appProperties")
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(item_value)
            for key, item_value in value.items()
            if isinstance(key, str) and item_value is not None
        }

    def _patch_workspace_properties(
        self,
        root: dict[str, Any],
        properties: dict[str, str],
    ) -> dict[str, Any]:
        patched = self._request(
            "PATCH",
            f"{_DRIVE_FILES}/{quote(str(root['id']), safe='')}",
            params={
                "fields": (
                    "id,name,mimeType,parents,createdTime,modifiedTime,size,trashed,"
                    "explicitlyTrashed,appProperties"
                ),
                "supportsAllDrives": "true",
            },
            json={"appProperties": properties},
        )
        verified = self._drive_item(str(root["id"]))
        return verified or patched

    def _is_workspace_snapshot(
        self,
        item: dict[str, Any] | None,
        *,
        root_id: str,
    ) -> bool:
        if item is None:
            return False
        properties = self._workspace_properties(item)
        return (
            item.get("mimeType") == _ZIP_MIME
            and item.get("trashed") is not True
            and self._string_list(item.get("parents")) == [root_id]
            and properties.get("llmOcrWorkspaceSnapshot") == _WORKSPACE_ROOT_MARKER
            and properties.get("llmOcrWorkspaceReady") == "true"
            and str(properties.get("llmOcrWorkspaceRevision") or "").isdigit()
            and bool(properties.get("llmOcrWorkspaceSha256"))
        )

    def _workspace_snapshot_view(self, item: dict[str, Any]) -> dict[str, Any]:
        properties = self._workspace_properties(item)
        return {
            "fileId": str(item.get("id") or ""),
            "fileName": str(item.get("name") or ""),
            "revision": int(properties.get("llmOcrWorkspaceRevision") or 0),
            "sha256": properties.get("llmOcrWorkspaceSha256") or "",
            "deviceId": properties.get("llmOcrWorkspaceDeviceId") or "",
            "createdAt": (
                properties.get("llmOcrWorkspaceCreatedAt")
                or item.get("createdTime")
                or item.get("modifiedTime")
            ),
            "fileBytes": int(item.get("size") or 0),
        }

    def _find_spreadsheet_outputs(
        self,
        operation_id: str,
        document_id: str,
    ) -> list[dict[str, Any]]:
        escaped_operation = operation_id.replace("\\", "\\\\").replace("'", "\\'")
        escaped_document = document_id.replace("\\", "\\\\").replace("'", "\\'")
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {
                "q": (
                    f"mimeType = '{_SPREADSHEET_MIME}' and "
                    "appProperties has { "
                    "key='llmOcrSpreadsheetOperationId' and "
                    f"value='{escaped_operation}' }} and "
                    "appProperties has { "
                    "key='llmOcrLedgerDocumentId' and "
                    f"value='{escaped_document}' }}"
                ),
                "fields": (
                    "nextPageToken,files("
                    "id,name,mimeType,parents,modifiedTime,trashed,"
                    "explicitlyTrashed,appProperties)"
                ),
                "pageSize": "1000",
                "spaces": "drive",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._request("GET", _DRIVE_FILES, params=params)
            page_items = payload.get("files")
            if isinstance(page_items, list):
                for item in page_items:
                    if not isinstance(item, dict):
                        continue
                    properties = item.get("appProperties")
                    if (
                        item.get("mimeType") == _SPREADSHEET_MIME
                        and isinstance(properties, dict)
                        and properties.get("llmOcrSpreadsheetOperationId")
                        == operation_id
                        and properties.get("llmOcrLedgerDocumentId") == document_id
                    ):
                        items.append(item)
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return items
            page_token = next_token

    def _compensate_library_document_move(
        self,
        file_id: str,
        *,
        original_parent: str,
        attempted_parent: str,
        created_folder_ids: list[str],
    ) -> bool:
        compensation_ok = True
        try:
            current = self._drive_item(file_id)
            current_parents = (
                self._string_list(current.get("parents"))
                if current is not None
                else []
            )
            if current_parents == [attempted_parent] and attempted_parent != original_parent:
                self._move_drive_item(
                    file_id,
                    original_parent,
                    current_parents,
                )
            elif current_parents != [original_parent]:
                compensation_ok = False
        except Exception:
            compensation_ok = False

        created_ids = set(created_folder_ids)
        for folder_id in reversed(created_folder_ids):
            try:
                folder = self._drive_item(folder_id)
                if folder is None or folder.get("trashed") is True:
                    continue
                children = self._list_drive_children(folder_id, include_trashed=True)
                if any(str(child.get("id") or "") not in created_ids for child in children):
                    compensation_ok = False
                    continue
                self._request(
                    "PATCH",
                    f"{_DRIVE_FILES}/{quote(folder_id, safe='')}",
                    params={
                        "fields": "id,trashed",
                        "supportsAllDrives": "true",
                    },
                    json={"trashed": True},
                )
                final_folder = self._drive_item(folder_id)
                if final_folder is None or final_folder.get("trashed") is not True:
                    compensation_ok = False
            except Exception:
                compensation_ok = False
        return compensation_ok

    def _move_drive_item(
        self,
        file_id: str,
        parent_id: str,
        current_parents: list[str],
    ) -> dict[str, Any]:
        params = {
            "addParents": parent_id,
            "fields": (
                "id,name,mimeType,parents,modifiedTime,trashed,"
                "explicitlyTrashed,appProperties"
            ),
            "supportsAllDrives": "true",
        }
        if current_parents:
            params["removeParents"] = ",".join(current_parents)
        moved = self._request(
            "PATCH",
            f"{_DRIVE_FILES}/{quote(file_id, safe='')}",
            params=params,
            json={},
        )
        if (
            str(moved.get("id") or "") != file_id
            or self._string_list(moved.get("parents")) != [parent_id]
            or moved.get("trashed") is True
        ):
            raise AppError(
                status=409,
                code="DRIVE_ITEM_MOVE_CONFLICT",
                title="Drive item move could not be verified",
                detail="Google Drive側の移動結果を一意に確認できませんでした。",
            )
        final_item = self._drive_item(file_id)
        if (
            final_item is None
            or not self._drive_item_matches_snapshot(final_item, moved)
            or self._string_list(final_item.get("parents")) != [parent_id]
            or final_item.get("trashed") is True
        ):
            raise AppError(
                status=409,
                code="DRIVE_ITEM_MOVE_CONFLICT",
                title="Drive item move changed after update",
                detail="移動処理中にGoogle Drive側の保存場所が変更されました。",
            )
        return final_item

    def _drive_item_matches_snapshot(
        self,
        current: dict[str, Any] | None,
        snapshot: dict[str, Any],
    ) -> bool:
        if current is None:
            return False
        return (
            current.get("id") == snapshot.get("id")
            and current.get("name") == snapshot.get("name")
            and current.get("mimeType") == snapshot.get("mimeType")
            and self._string_list(current.get("parents"))
            == self._string_list(snapshot.get("parents"))
            and current.get("modifiedTime") == snapshot.get("modifiedTime")
            and current.get("trashed") == snapshot.get("trashed")
            and current.get("explicitlyTrashed")
            == snapshot.get("explicitlyTrashed")
            and current.get("appProperties") == snapshot.get("appProperties")
        )

    def _update_mapping_snapshot(
        self,
        mapping: DocumentFolderDriveMapping,
        item: dict[str, Any],
    ) -> None:
        mapping.drive_name = str(item.get("name") or mapping.drive_name)
        parents = self._string_list(item.get("parents"))
        if parents:
            mapping.drive_parent_folder_id = parents[0]
        mapping.drive_modified_at = self._drive_datetime(item.get("modifiedTime"))
        mapping.last_synced_at = datetime.now(UTC)

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str) and item]

    @staticmethod
    def _drive_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _drive_folder_name(value: str) -> str | None:
        cleaned = " ".join(value.split())
        if (
            not cleaned
            or len(cleaned) > 100
            or cleaned in {".", ".."}
            or "/" in cleaned
            or "\\" in cleaned
        ):
            return None
        return cleaned

    @staticmethod
    def _drive_document_name(value: str) -> str | None:
        cleaned = " ".join(value.split())
        cleaned = re.sub(r"\.xlsx$", "", cleaned, flags=re.IGNORECASE).strip()
        if (
            not cleaned
            or len(cleaned) > 180
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', cleaned)
        ):
            return None
        return cleaned

    @staticmethod
    def _folder_name_exists(
        session: Session,
        parent_id: str | None,
        normalized_name: str,
        *,
        exclude_id: str | None = None,
    ) -> bool:
        parent_condition = (
            DocumentFolder.parent_id.is_(None)
            if parent_id is None
            else DocumentFolder.parent_id == parent_id
        )
        statement = select(DocumentFolder.id).where(
            parent_condition,
            DocumentFolder.normalized_name == normalized_name,
        )
        if exclude_id is not None:
            statement = statement.where(DocumentFolder.id != exclude_id)
        return session.scalar(statement.limit(1)) is not None

    @staticmethod
    def _would_create_folder_cycle(
        session: Session,
        folder_id: str,
        parent_id: str | None,
    ) -> bool:
        current_id = parent_id
        visited: set[str] = set()
        while current_id is not None:
            if current_id == folder_id or current_id in visited:
                return True
            visited.add(current_id)
            parent = session.get(DocumentFolder, current_id)
            current_id = parent.parent_id if parent is not None else None
        return False

    @staticmethod
    def _library_folder_path(session: Session, folder_id: str | None) -> str | None:
        names: list[str] = []
        current_id = folder_id
        visited: set[str] = set()
        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            folder = session.get(DocumentFolder, current_id)
            if folder is None:
                break
            names.append(folder.name)
            current_id = folder.parent_id
        return "/".join(reversed(names)) or None

    @staticmethod
    def _folders_parent_first(folders: list[DocumentFolder]) -> list[DocumentFolder]:
        by_id = {folder.id: folder for folder in folders}
        children: defaultdict[str, list[DocumentFolder]] = defaultdict(list)
        ready: list[DocumentFolder] = []
        for folder in folders:
            if folder.parent_id is not None and folder.parent_id in by_id:
                children[folder.parent_id].append(folder)
            else:
                ready.append(folder)
        def sort_key(folder: DocumentFolder) -> tuple[datetime, str]:
            return folder.created_at, folder.id

        for siblings in children.values():
            siblings.sort(key=sort_key)
        pending = deque(sorted(ready, key=sort_key))
        ordered: list[DocumentFolder] = []
        visited: set[str] = set()
        while pending:
            folder = pending.popleft()
            if folder.id in visited:
                continue
            visited.add(folder.id)
            ordered.append(folder)
            pending.extend(children.get(folder.id, ()))
        # Preserve deterministic cycle handling; _ensure reports the actual
        # hierarchy error before any affected folder is changed.
        ordered.extend(sorted(
            (folder for folder in folders if folder.id not in visited),
            key=sort_key,
        ))
        return ordered

    @staticmethod
    def _record_library_conflict(
        result: dict[str, Any],
        *,
        code: str,
        resource_id: str,
        detail: str,
        mapping_id: str | None = None,
        resource_name: str | None = None,
        drive_root_id: str | None = None,
    ) -> None:
        details = result.setdefault("conflictItems", [])
        if not isinstance(details, list):
            return
        conflict = {
            "code": code,
            "resourceId": resource_id,
            "detail": detail,
        }
        if resource_name:
            conflict["resourceName"] = resource_name
        if mapping_id:
            conflict["mappingId"] = mapping_id
        if drive_root_id:
            conflict["driveRootId"] = drive_root_id
            root_names = result.get("_rootNames")
            if isinstance(root_names, dict):
                root_name = root_names.get(drive_root_id)
                if isinstance(root_name, str) and root_name:
                    conflict["driveRootName"] = root_name
        if conflict not in details:
            details.append(conflict)
            result["conflicts"] = len(details)
        if mapping_id is not None:
            skipped = result.setdefault("_skipMappingIds", [])
            if isinstance(skipped, list) and mapping_id not in skipped:
                skipped.append(mapping_id)

    def _account_snapshot(
        self,
        expected_subject: str | None = None,
    ) -> dict[str, str | None]:
        subject = self._require_subject(expected_subject)
        with self._database.session() as session:
            account = session.get(GoogleOAuthAccount, subject)
            if account is None:
                raise self._not_connected()
            return {
                "selectedId": account.default_output_folder_id,
                "selectedName": account.default_output_folder_name,
            }

    def _active_account(
        self,
        session: Session,
        expected_subject: str | None = None,
    ) -> GoogleOAuthAccount:
        subject = expected_subject or self._require_subject()
        account = session.get(GoogleOAuthAccount, subject)
        if account is None:
            raise self._not_connected()
        return account

    def _document_title(
        self,
        session: Session,
        ledger: LedgerDocument,
        fallback: str,
    ) -> str:
        identity = ledger.current_document_json.get("document", {})
        name = self._reading(identity.get("cattleName")) if isinstance(identity, dict) else None
        template = session.get(Template, ledger.template_id)
        parts = [ledger.individual_id, name, template.name if template else fallback]
        return "_".join(re.sub(r"[\\/:*?\"<>|]", "_", part).strip() for part in parts if part)[:180]

    @staticmethod
    def _reading(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        candidate = value.get("normalized") or value.get("raw")
        return str(candidate).strip() if candidate else None

    def _create_file(
        self,
        *,
        name: str,
        mime_type: str,
        parent_id: str,
        app_properties: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "mimeType": mime_type,
            "parents": [parent_id],
        }
        if app_properties:
            body["appProperties"] = app_properties
        return self._request(
            "POST",
            _DRIVE_FILES,
            params={
                "fields": (
                    "id,name,mimeType,parents,modifiedTime,trashed,"
                    "explicitlyTrashed,appProperties"
                ),
                "supportsAllDrives": "true",
            },
            json=body,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        allow_not_found: bool = False,
        **kwargs: object,
    ) -> dict[str, Any]:
        try:
            response = self._authorized_session().request(  # type: ignore[no-untyped-call]
                method,
                url,
                timeout=30,
                **kwargs,
            )
        except GoogleAuthError as exc:
            self._session = None
            self._session_subject = None
            raise AppError(
                status=401,
                code="GOOGLE_AUTH_REFRESH_FAILED",
                title="Google authorization refresh failed",
                detail="Google連携を更新できませんでした。連携をやり直してください。",
            ) from exc
        if response.status_code == 404 and allow_not_found:
            return {}
        if response.status_code == 401:
            self._session = None
            self._session_subject = None
            raise AppError(
                status=401,
                code="GOOGLE_AUTH_REAUTHORIZATION_REQUIRED",
                title="Google authorization is no longer valid",
                detail="Google連携の有効期限が切れています。連携をやり直してください。",
            )
        if (
            response.status_code in {400, 410}
            and url == _DRIVE_CHANGES
            and method == "GET"
        ):
            raise AppError(
                status=409,
                code="DRIVE_CHANGE_TOKEN_INVALID",
                title="Drive change token is invalid",
                detail=(
                    "Google Driveの差分同期位置を再利用できないため、"
                    "次の処理で全件同期へ戻します。"
                ),
            )
        if response.status_code >= 400:
            raise AppError(
                status=502,
                code="GOOGLE_DRIVE_ERROR",
                title="Google Drive request failed",
                detail=(
                    "Google Driveのフォルダまたはファイルを操作できませんでした。"
                    "Google連携をやり直してください。"
                ),
            )
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _raw_drive_request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> Any:
        timeout = kwargs.pop("timeout", 30)
        try:
            response = self._authorized_session().request(  # type: ignore[no-untyped-call]
                method,
                url,
                timeout=timeout,
                **kwargs,
            )
        except GoogleAuthError as exc:
            self._session = None
            self._session_subject = None
            raise AppError(
                status=401,
                code="GOOGLE_AUTH_REFRESH_FAILED",
                title="Google authorization refresh failed",
                detail="Google連携を更新できませんでした。連携をやり直してください。",
            ) from exc
        if response.status_code == 401:
            self._session = None
            self._session_subject = None
            raise AppError(
                status=401,
                code="GOOGLE_AUTH_REAUTHORIZATION_REQUIRED",
                title="Google authorization is no longer valid",
                detail="Google連携の有効期限が切れています。連携をやり直してください。",
            )
        if response.status_code == 412:
            raise self._template_draft_conflict()
        if response.status_code >= 400:
            raise AppError(
                status=502,
                code="GOOGLE_DRIVE_ERROR",
                title="Google Drive request failed",
                detail=(
                    "Google Driveの同期用ファイルを操作できませんでした。"
                    "Google連携を確認してください。"
                ),
            )
        return response

    def _authorized_session(
        self,
        expected_subject: str | None = None,
    ) -> AuthorizedSession:
        subject = expected_subject or self._request_subject or self._require_subject()
        if self._session is None or self._session_subject != subject:
            self._session = AuthorizedSession(  # type: ignore[no-untyped-call]
                self._credentials_provider(subject)
            )
            self._session_subject = subject
        return self._session

    def _require_subject(self, expected_subject: str | None = None) -> str:
        subject = self._subject_provider()
        if not subject:
            raise self._not_connected()
        if expected_subject is not None and subject != expected_subject:
            raise self._not_connected()
        return subject

    @staticmethod
    def _spreadsheet_url(spreadsheet_id: str) -> str:
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

    @staticmethod
    def _not_connected() -> AppError:
        return AppError(
            status=401,
            code="GOOGLE_ACCOUNT_REQUIRED",
            title="Google account is required",
            detail="Googleアカウントを接続してください。",
        )
