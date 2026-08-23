from __future__ import annotations

import re
from collections.abc import Callable
from threading import Lock
from typing import Any
from urllib.parse import quote

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import GoogleOAuthAccount, LedgerDocument, Template, TemplateOutputFolder
from app.db.session import Database

_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"


class GoogleDriveService:
    def __init__(
        self,
        database: Database,
        credentials_provider: Callable[[], Credentials],
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
        self._lock = Lock()

    def reset_authorization(self) -> None:
        with self._lock:
            self._session = None

    def output_folder(self) -> dict[str, Any]:
        snapshot = self._account_snapshot()
        return {
            "pickerConfigured": bool(
                self._picker_api_key
                and self._picker_app_id
                and self._picker_project_matches
            ),
            "pickerConfigurationError": self._picker_configuration_error,
            "folderId": snapshot["selectedId"],
            "folderName": snapshot["selectedName"],
        }

    def picker_session(self) -> dict[str, str]:
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
        credentials = self._credentials_provider()
        try:
            credentials.refresh(GoogleAuthRequest())  # type: ignore[no-untyped-call]
        except GoogleAuthError as exc:
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

    def select_output_folder(self, folder_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._request(
                "GET",
                f"{_DRIVE_FILES}/{quote(folder_id, safe='')}",
                params={"fields": "id,name,mimeType,trashed,capabilities(canAddChildren)"},
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
            subject = self._require_subject()
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
            return self.output_folder()

    def template_output_folder(self, template_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            template = session.get(Template, template_id)
            if template is None:
                raise AppError(
                    status=404,
                    code="TEMPLATE_NOT_FOUND",
                    title="Template not found",
                    detail="指定されたテンプレートが見つかりません。",
                )
            account = self._active_account(session)
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
    ) -> dict[str, Any]:
        with self._lock:
            item = self._request(
                "GET",
                f"{_DRIVE_FILES}/{quote(folder_id, safe='')}",
                params={"fields": "id,name,mimeType,trashed,capabilities(canAddChildren)"},
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
                account = self._active_account(session)
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
            return self.template_output_folder(template_id)

    def document_target(self, document_id: str | None) -> tuple[str | None, str | None]:
        if not document_id:
            return None, None
        with self._database.session() as session:
            ledger = session.get(LedgerDocument, document_id)
            if ledger is None:
                return None, None
            return ledger.spreadsheet_id, ledger.spreadsheet_url

    def spreadsheet_targets(self) -> list[tuple[str, str]]:
        with self._database.session() as session:
            rows = session.execute(
                select(LedgerDocument.spreadsheet_id, LedgerDocument.spreadsheet_url).where(
                    LedgerDocument.spreadsheet_id.is_not(None)
                )
            ).all()
            return [
                (spreadsheet_id, spreadsheet_url or self._spreadsheet_url(spreadsheet_id))
                for spreadsheet_id, spreadsheet_url in rows
                if isinstance(spreadsheet_id, str)
            ]

    def create_document_spreadsheet(
        self,
        document_id: str,
        fallback_title: str,
    ) -> tuple[str, str]:
        with self._lock:
            with self._database.session() as session:
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None:
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
                account = self._active_account(session)
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
                title = self._document_title(session, ledger, fallback_title)
            created = self._create_file(
                name=title,
                mime_type=_SPREADSHEET_MIME,
                parent_id=folder_id,
            )
            spreadsheet_id = str(created["id"])
            spreadsheet_url = self._spreadsheet_url(spreadsheet_id)
            with self._database.session() as session:
                ledger = session.get(LedgerDocument, document_id)
                if ledger is None:
                    raise RuntimeError("台帳が削除されたためDriveファイルを関連付けできません。")
                if ledger.spreadsheet_id:
                    return (
                        ledger.spreadsheet_id,
                        ledger.spreadsheet_url or self._spreadsheet_url(ledger.spreadsheet_id),
                    )
                ledger.spreadsheet_id = spreadsheet_id
                ledger.spreadsheet_url = spreadsheet_url
                ledger.drive_folder_id = folder_id
                ledger.drive_folder_path = folder_path
                ledger.spreadsheet_sync_status = "CREATED"
                ledger.spreadsheet_sync_error = None
                session.commit()
            return spreadsheet_id, spreadsheet_url

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

    def _account_snapshot(self) -> dict[str, str | None]:
        subject = self._require_subject()
        with self._database.session() as session:
            account = session.get(GoogleOAuthAccount, subject)
            if account is None:
                raise self._not_connected()
            return {
                "selectedId": account.default_output_folder_id,
                "selectedName": account.default_output_folder_name,
            }

    def _active_account(self, session: Session) -> GoogleOAuthAccount:
        subject = self._require_subject()
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

    def _create_file(self, *, name: str, mime_type: str, parent_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            _DRIVE_FILES,
            params={"fields": "id,name,parents"},
            json={"name": name, "mimeType": mime_type, "parents": [parent_id]},
        )

    def _request(self, method: str, url: str, **kwargs: object) -> dict[str, Any]:
        response = self._authorized_session().request(  # type: ignore[no-untyped-call]
            method,
            url,
            timeout=30,
            **kwargs,
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

    def _authorized_session(self) -> AuthorizedSession:
        if self._session is None:
            self._session = AuthorizedSession(self._credentials_provider())  # type: ignore[no-untyped-call]
        return self._session

    def _require_subject(self) -> str:
        subject = self._subject_provider()
        if not subject:
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
