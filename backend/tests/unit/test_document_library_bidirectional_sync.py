from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest
from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import (
    ConfirmedResult,
    DocumentFolder,
    DocumentFolderDriveMapping,
    DocumentLibrarySyncState,
    GoogleOAuthAccount,
    LedgerDocument,
    ProcessingJob,
    SpreadsheetOutputOperation,
    Template,
    TemplateOutputFolder,
)
from app.db.session import Database
from app.services.document_library_service import DocumentLibraryService
from app.services.google_drive_service import GoogleDriveService

_FOLDER_MIME = "application/vnd.google-apps.folder"
_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"


class _FakeDrive:
    """Small in-memory Drive v3 adapter used at the GoogleDriveService boundary."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._changes: list[dict[str, Any]] = []
        self._next_id = 1
        self.fail_next_file_create = False
        self.fail_next_file_create_after_apply = False
        self.fail_next_change_list_invalid = False
        self.fail_next_file_update = False
        self.fail_file_create_parent_id: str | None = None
        self.fail_file_update_id: str | None = None
        self.fail_file_update_after_apply_id: str | None = None
        self.fail_file_move_id: str | None = None
        self.fail_next_upload_after_apply = False
        self.add_folder("root-a", "共通保存先", parents=[], record_change=False)
        self.add_folder("root-b", "テンプレート別保存先", parents=[], record_change=False)

    def add_folder(
        self,
        file_id: str,
        name: str,
        *,
        parents: list[str],
        record_change: bool = True,
    ) -> dict[str, Any]:
        return self._add_file(
            file_id,
            name,
            mime_type=_FOLDER_MIME,
            parents=parents,
            record_change=record_change,
        )

    def add_spreadsheet(
        self,
        file_id: str,
        name: str,
        *,
        parents: list[str],
        record_change: bool = True,
    ) -> dict[str, Any]:
        return self._add_file(
            file_id,
            name,
            mime_type=_SPREADSHEET_MIME,
            parents=parents,
            record_change=record_change,
        )

    def external_update(
        self,
        file_id: str,
        *,
        name: str | None = None,
        parents: list[str] | None = None,
    ) -> None:
        item = self.files[file_id]
        if name is not None:
            item["name"] = name
        if parents is not None:
            item["parents"] = list(parents)
        self._record_change(file_id)

    def external_remove(self, file_id: str) -> None:
        self.files[file_id]["trashed"] = True
        self.files[file_id]["explicitlyTrashed"] = True
        for item in self.files.values():
            if file_id in item.get("parents", []):
                item["trashed"] = True
                item["explicitlyTrashed"] = False
        self._record_change(file_id, removed=True)

    def find_one(
        self,
        *,
        name: str,
        parent_id: str,
        include_trashed: bool = False,
    ) -> dict[str, Any]:
        matches = [
            item
            for item in self.files.values()
            if item["name"] == name
            and parent_id in item.get("parents", [])
            and (include_trashed or not item.get("trashed", False))
        ]
        assert len(matches) == 1, (
            f"Expected one Drive item named {name!r} under {parent_id!r}; "
            f"found {len(matches)}"
        )
        return matches[0]

    def request(self, method: str, url: str, **kwargs: object) -> dict[str, Any]:
        typed_kwargs = {key: value for key, value in kwargs.items()}
        self.calls.append((method, url, typed_kwargs))
        params = kwargs.get("params")
        query = params if isinstance(params, dict) else {}
        body = kwargs.get("json")
        payload = body if isinstance(body, dict) else {}

        if url.endswith("/changes/startPageToken"):
            return {"startPageToken": str(len(self._changes))}
        if url.endswith("/changes"):
            if self.fail_next_change_list_invalid:
                self.fail_next_change_list_invalid = False
                raise AppError(
                    status=409,
                    code="DRIVE_CHANGE_TOKEN_INVALID",
                    title="Drive change token is invalid",
                    detail="simulated invalid Drive change token",
                )
            start = int(str(query.get("pageToken", "0")))
            changes = deepcopy(self._changes[start:])
            return {
                "changes": changes,
                "newStartPageToken": str(len(self._changes)),
            }

        file_match = re.search(r"/files/([^/?]+)$", url)
        if file_match:
            file_id = file_match.group(1)
            if method == "GET":
                return deepcopy(self.files[file_id])
            if method == "PATCH":
                if (
                    self.fail_file_move_id == file_id
                    and str(query.get("addParents", ""))
                ):
                    self.fail_file_move_id = None
                    raise AppError(
                        status=502,
                        code="GOOGLE_DRIVE_ERROR",
                        title="Google Drive request failed",
                        detail="simulated Drive move failure",
                    )
                if self.fail_next_file_update or self.fail_file_update_id == file_id:
                    self.fail_next_file_update = False
                    self.fail_file_update_id = None
                    raise AppError(
                        status=502,
                        code="GOOGLE_DRIVE_ERROR",
                        title="Google Drive request failed",
                        detail="simulated Drive update failure",
                    )
                item = self.files[file_id]
                if "name" in payload:
                    item["name"] = payload["name"]
                if "appProperties" in payload:
                    item["appProperties"] = {
                        **item.get("appProperties", {}),
                        **payload["appProperties"],
                    }
                if "parents" in payload:
                    item["parents"] = list(payload["parents"])
                remove_parents = str(query.get("removeParents", ""))
                if remove_parents:
                    removed = set(remove_parents.split(","))
                    item["parents"] = [
                        parent for parent in item.get("parents", []) if parent not in removed
                    ]
                add_parents = str(query.get("addParents", ""))
                if add_parents:
                    for parent in add_parents.split(","):
                        if parent and parent not in item.get("parents", []):
                            item.setdefault("parents", []).append(parent)
                if isinstance(payload.get("trashed"), bool):
                    item["trashed"] = payload["trashed"]
                    item["explicitlyTrashed"] = payload["trashed"]
                self._record_change(file_id, removed=bool(item.get("trashed")))
                if self.fail_file_update_after_apply_id == file_id:
                    self.fail_file_update_after_apply_id = None
                    raise AppError(
                        status=502,
                        code="GOOGLE_DRIVE_ERROR",
                        title="Google Drive response was lost",
                        detail="simulated Drive update failure after apply",
                    )
                return deepcopy(item)
            if method == "DELETE":
                self.files[file_id]["trashed"] = True
                self._record_change(file_id, removed=True)
                return {}

        if "/upload/drive/v3/files" in url and method == "POST":
            data = kwargs.get("data")
            assert isinstance(data, bytes)
            metadata_bytes = data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
            metadata = json.loads(metadata_bytes.decode("utf-8"))
            assert isinstance(metadata, dict)
            parent_ids = [str(parent) for parent in metadata.get("parents", [])]
            file_id = f"created-{self._next_id}"
            self._next_id += 1
            created = self._add_file(
                file_id,
                str(metadata["name"]),
                mime_type=str(metadata.get("mimeType", _SPREADSHEET_MIME)),
                parents=parent_ids,
                record_change=True,
            )
            if isinstance(metadata.get("appProperties"), dict):
                created["appProperties"] = deepcopy(metadata["appProperties"])
            if self.fail_next_upload_after_apply:
                self.fail_next_upload_after_apply = False
                raise AppError(
                    status=502,
                    code="GOOGLE_DRIVE_ERROR",
                    title="Google Drive response was lost",
                    detail="simulated upload response loss",
                )
            return deepcopy(created)
        if url.endswith("/files") and method == "POST":
            parent_ids = [str(parent) for parent in payload.get("parents", [])]
            if (
                self.fail_next_file_create
                or self.fail_file_create_parent_id in parent_ids
            ):
                self.fail_next_file_create = False
                self.fail_file_create_parent_id = None
                raise AppError(
                    status=502,
                    code="GOOGLE_DRIVE_ERROR",
                    title="Google Drive request failed",
                    detail="simulated Drive create failure",
                )
            file_id = f"created-{self._next_id}"
            self._next_id += 1
            created = self._add_file(
                file_id,
                str(payload["name"]),
                mime_type=str(payload.get("mimeType", _FOLDER_MIME)),
                parents=parent_ids,
                record_change=True,
            )
            if isinstance(payload.get("appProperties"), dict):
                created["appProperties"] = deepcopy(payload["appProperties"])
            if self.fail_next_file_create_after_apply:
                self.fail_next_file_create_after_apply = False
                raise AppError(
                    status=502,
                    code="GOOGLE_DRIVE_ERROR",
                    title="Google Drive response was lost",
                    detail="simulated file create response loss",
                )
            return deepcopy(created)
        if url.endswith("/files") and method == "GET":
            return {"files": self._list_files(str(query.get("q", "")))}

        raise AssertionError(f"Unexpected Drive request: {method} {url} {kwargs!r}")

    def _add_file(
        self,
        file_id: str,
        name: str,
        *,
        mime_type: str,
        parents: list[str],
        record_change: bool,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": file_id,
            "name": name,
            "mimeType": mime_type,
            "parents": list(parents),
            "trashed": False,
            "appProperties": {},
            "capabilities": {"canAddChildren": True, "canMoveItemWithinDrive": True},
        }
        self.files[file_id] = item
        if record_change:
            self._record_change(file_id)
        return item

    def _list_files(self, query: str) -> list[dict[str, Any]]:
        parent_match = re.search(r"'([^']+)'\s+in\s+parents", query)
        parent_id = parent_match.group(1) if parent_match else None
        mime_match = re.search(r"mimeType\s*=\s*'([^']+)'", query)
        mime_type = mime_match.group(1) if mime_match else None
        return [
            deepcopy(item)
            for item in self.files.values()
            if ("trashed = false" not in query or not item.get("trashed", False))
            and (parent_id is None or parent_id in item.get("parents", []))
            and (mime_type is None or item.get("mimeType") == mime_type)
        ]

    def _record_change(self, file_id: str, *, removed: bool = False) -> None:
        change: dict[str, Any] = {
            "changeType": "file",
            "fileId": file_id,
            "removed": removed,
        }
        if not removed:
            change["file"] = deepcopy(self.files[file_id])
        self._changes.append(change)


def _database(settings_factory: Callable[..., Settings]) -> Database:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    return database


def _drive_service(
    database: Database,
    fake: _FakeDrive,
    monkeypatch: pytest.MonkeyPatch,
) -> GoogleDriveService:
    def credentials_provider(_subject: str | None = None) -> Credentials:
        raise AssertionError("Google API transport must be replaced in this test.")

    drive = GoogleDriveService(database, credentials_provider, lambda: "subject-1")
    monkeypatch.setattr(drive, "_request", fake.request)
    return drive


def _seed_two_roots(database: Database) -> tuple[str, str]:
    with database.session() as session:
        default_template = Template(name="共通帳票", document_type="default")
        other_template = Template(name="別帳票", document_type="other")
        duplicate_root_template = Template(name="同一保存先帳票", document_type="duplicate-root")
        session.add_all((default_template, other_template, duplicate_root_template))
        session.flush()
        session.add(
            GoogleOAuthAccount(
                google_subject="subject-1",
                email="user@example.com",
                encrypted_refresh_token="encrypted",
                scopes="https://www.googleapis.com/auth/drive.file",
                drive_root_folder_id="root-a",
                drive_root_folder_name="共通保存先",
                default_output_folder_id="root-a",
                default_output_folder_name="共通保存先",
                default_output_folder_path="共通保存先",
            )
        )
        session.add_all(
            (
                TemplateOutputFolder(
                    google_subject="subject-1",
                    template_id=other_template.id,
                    folder_id="root-b",
                    folder_name="テンプレート別保存先",
                    folder_path="テンプレート別保存先",
                ),
                TemplateOutputFolder(
                    google_subject="subject-1",
                    template_id=duplicate_root_template.id,
                    folder_id="root-b",
                    folder_name="テンプレート別保存先",
                    folder_path="テンプレート別保存先",
                ),
            )
        )
        session.commit()
        return default_template.id, other_template.id


def _create_local_folder(
    database: Database,
    name: str,
    *,
    parent_id: str | None = None,
) -> str:
    with database.session() as session:
        folder = DocumentFolder(
            parent_id=parent_id,
            name=name,
            normalized_name=name.casefold(),
        )
        session.add(folder)
        session.commit()
        return folder.id


def _seed_document(
    database: Database,
    *,
    template_id: str,
    spreadsheet_id: str,
    drive_parent_id: str,
    library_folder_id: str | None = None,
    drive_google_subject: str | None = "subject-1",
) -> str:
    with database.session() as session:
        document = LedgerDocument(
            template_id=template_id,
            individual_id="1234567890",
            current_document_json={},
            spreadsheet_id=spreadsheet_id,
            spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            drive_google_subject=drive_google_subject,
            drive_root_folder_id=drive_parent_id,
            drive_folder_id=drive_parent_id,
            drive_folder_path=drive_parent_id,
            library_folder_id=library_folder_id,
            library_location_version=1,
        )
        session.add(document)
        session.commit()
        return document.id


def _attach_confirmed_result(
    database: Database,
    *,
    document_id: str,
    spreadsheet_id: str,
    spreadsheet_name: str,
) -> str:
    with database.session() as session:
        job = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            status="CONFIRMED",
            request_hash="c" * 64,
            config_snapshot_json={},
        )
        session.add(job)
        session.flush()
        document = session.get(LedgerDocument, document_id)
        assert document is not None
        document.current_job_id = job.id
        session.add(
            ConfirmedResult(
                job_id=job.id,
                ocr_result_revision=1,
                individual_id=document.individual_id,
                confirmed_json={},
                export_relative_path="exports/confirmed.json",
                spreadsheet_id=spreadsheet_id,
                spreadsheet_url=(
                    f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
                ),
                spreadsheet_name=spreadsheet_name,
            )
        )
        session.commit()
        return job.id


def _attach_pending_confirmed_result(
    database: Database,
    *,
    document_id: str,
    revision: int = 1,
) -> str:
    with database.session() as session:
        document = session.get(LedgerDocument, document_id)
        assert document is not None
        job = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            status="CONFIRMED",
            request_hash=f"{revision:064x}"[-64:],
            config_snapshot_json={},
        )
        session.add(job)
        session.flush()
        document.current_job_id = job.id
        session.add(
            ConfirmedResult(
                job_id=job.id,
                ocr_result_revision=revision,
                individual_id=document.individual_id,
                confirmed_json={},
                export_relative_path="exports/confirmed.json",
            )
        )
        session.commit()
        return job.id


def _seed_unbound_document(
    database: Database,
    *,
    template_id: str,
    revision: int = 1,
) -> tuple[str, str]:
    with database.session() as session:
        document = LedgerDocument(
            template_id=template_id,
            individual_id="1234567890",
            current_document_json={},
        )
        session.add(document)
        session.commit()
        document_id = document.id
    job_id = _attach_pending_confirmed_result(
        database,
        document_id=document_id,
        revision=revision,
    )
    return document_id, job_id


def _seed_legacy_spreadsheet_output_for_rename(
    database: Database,
    fake: _FakeDrive,
    *,
    spreadsheet_id: str,
) -> tuple[str, str, str]:
    template_id, _ = _seed_two_roots(database)
    fake.add_spreadsheet(
        spreadsheet_id,
        "変更前",
        parents=["root-a"],
        record_change=False,
    )
    document_id = _seed_document(
        database,
        template_id=template_id,
        spreadsheet_id=spreadsheet_id,
        drive_parent_id="root-a",
    )
    job_id = _attach_confirmed_result(
        database,
        document_id=document_id,
        spreadsheet_id=spreadsheet_id,
        spreadsheet_name="変更後",
    )
    with database.session() as session:
        document = session.get(LedgerDocument, document_id)
        confirmed = session.scalar(
            select(ConfirmedResult).where(ConfirmedResult.job_id == job_id)
        )
        assert document is not None
        assert confirmed is not None
        document.document_name = "変更後"
        document.library_name = "変更後"
        confirmed.spreadsheet_revision = 1
        session.commit()
    return template_id, document_id, job_id


def test_mirror_folder_creates_one_idempotent_copy_per_distinct_root(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "2026年度")
        drive = _drive_service(database, fake, monkeypatch)

        drive.mirror_library_folder(folder_id)
        drive.mirror_library_folder(folder_id)

        assert fake.find_one(name="2026年度", parent_id="root-a")
        assert fake.find_one(name="2026年度", parent_id="root-b")
        folder_creates = [
            call
            for call in fake.calls
            if call[0] == "POST"
            and call[1].endswith("/files")
            and isinstance(call[2].get("json"), dict)
            and call[2]["json"].get("name") == "2026年度"
        ]
        assert len(folder_creates) == 2
    finally:
        database.dispose()


def test_library_create_and_rename_are_applied_to_every_drive_root(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        library = DocumentLibraryService(database, google_drive=drive)

        created = library.create_folder(name="作成直後", parent_id=None)

        assert fake.find_one(name="作成直後", parent_id="root-a")
        assert fake.find_one(name="作成直後", parent_id="root-b")

        renamed = library.rename_folder(created["id"], name="名前変更後")

        assert renamed["name"] == "名前変更後"
        assert fake.find_one(name="名前変更後", parent_id="root-a")
        assert fake.find_one(name="名前変更後", parent_id="root-b")
        assert not [
            item
            for item in fake.files.values()
            if item["name"] == "作成直後" and not item.get("trashed", False)
        ]
    finally:
        database.dispose()


def test_failed_library_folder_create_keeps_local_id_and_sync_can_retry(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        library = DocumentLibraryService(database, google_drive=drive)
        fake.fail_file_create_parent_id = "root-b"

        with pytest.raises(AppError, match="simulated Drive create failure"):
            library.create_folder(name="失敗後に再試行", parent_id=None)

        with database.session() as session:
            folder = session.query(DocumentFolder).filter_by(name="失敗後に再試行").one()
            folder_id = folder.id
        root_a_before_retry = fake.find_one(
            name="失敗後に再試行",
            parent_id="root-a",
        )

        result = drive.sync_document_library()

        with database.session() as session:
            assert session.get(DocumentFolder, folder_id) is not None
        assert result["status"] == "SYNCED"
        assert fake.find_one(name="失敗後に再試行", parent_id="root-a")["id"] == (
            root_a_before_retry["id"]
        )
        assert fake.find_one(name="失敗後に再試行", parent_id="root-b")
    finally:
        database.dispose()


def test_library_to_drive_push_creates_nested_folders_moves_sheet_and_is_idempotent(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _other_template_id = _seed_two_roots(database)
        parent_id = _create_local_folder(database, "年度")
        child_id = _create_local_folder(database, "申請書", parent_id=parent_id)
        fake.add_spreadsheet("sheet-push", "申請書", parents=["root-a"])
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-push",
            drive_parent_id="root-a",
            library_folder_id=child_id,
        )
        drive = _drive_service(database, fake, monkeypatch)

        first = drive.push_document_library_to_drive()

        assert first["mode"] == "LIBRARY_TO_DRIVE"
        assert first["status"] == "SYNCED"
        assert first["foldersImported"] == 0
        assert first["foldersMirrored"] == 4
        assert first["documentsUpdated"] == 1
        remote_parent = fake.find_one(name="年度", parent_id="root-a")
        remote_child = fake.find_one(
            name="申請書",
            parent_id=str(remote_parent["id"]),
        )
        assert fake.files["sheet-push"]["parents"] == [remote_child["id"]]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_folder_id == remote_child["id"]
            assert document.drive_folder_path == "年度/申請書"

        first_file_count = len(fake.files)
        second = drive.push_document_library_to_drive()

        assert second["status"] == "SYNCED"
        assert second["foldersMirrored"] == 0
        assert second["foldersUpdated"] == 0
        assert second["documentsUpdated"] == 0
        assert len(fake.files) == first_file_count
    finally:
        database.dispose()


def test_library_to_drive_push_does_not_overwrite_drive_folder_change(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "同期前")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        remote = fake.find_one(name="同期前", parent_id="root-a")
        fake.external_update(str(remote["id"]), name="Drive側変更")

        outcome = drive.push_document_library_to_drive()

        assert outcome["status"] == "PARTIAL"
        assert any(
            item["code"] == "DRIVE_FOLDER_CHANGED"
            and item["driveRootId"] == "root-a"
            for item in outcome["conflictItems"]
        )
        assert fake.files[str(remote["id"])]["name"] == "Drive側変更"
        with database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            assert folder is not None
            assert folder.name == "同期前"
    finally:
        database.dispose()


def test_library_to_drive_push_preserves_externally_moved_and_ownerless_sheets(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _other_template_id = _seed_two_roots(database)
        target_id = _create_local_folder(database, "移動先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(target_id)
        fake.add_folder("external-folder", "Drive独自", parents=["root-a"])
        fake.add_spreadsheet(
            "sheet-external",
            "外部移動済み",
            parents=["external-folder"],
        )
        _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-external",
            drive_parent_id="root-a",
            library_folder_id=target_id,
        )
        fake.add_spreadsheet(
            "sheet-ownerless",
            "所有者未確認",
            parents=["root-a"],
        )
        _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-ownerless",
            drive_parent_id="root-a",
            library_folder_id=target_id,
            drive_google_subject=None,
        )

        outcome = drive.push_document_library_to_drive()

        assert outcome["status"] == "PARTIAL"
        assert {item["code"] for item in outcome["conflictItems"]} >= {
            "DRIVE_DOCUMENT_LOCATION_CONFLICT",
            "DRIVE_DOCUMENT_OWNER_UNCONFIRMED",
        }
        assert fake.files["sheet-external"]["parents"] == ["external-folder"]
        assert fake.files["sheet-ownerless"]["parents"] == ["root-a"]
    finally:
        database.dispose()


def test_multi_root_rename_compensates_when_later_drive_update_fails(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        library = DocumentLibraryService(database, google_drive=drive)
        created = library.create_folder(name="変更前", parent_id=None)
        root_b = fake.find_one(name="変更前", parent_id="root-b")
        fake.fail_file_update_after_apply_id = str(root_b["id"])

        with pytest.raises(AppError, match="simulated Drive update failure after apply"):
            library.rename_folder(created["id"], name="変更後")

        with database.session() as session:
            folder = session.get(DocumentFolder, created["id"])
            assert folder is not None
            assert folder.name == "変更前"
        assert fake.find_one(name="変更前", parent_id="root-a")
        assert fake.find_one(name="変更前", parent_id="root-b")
        assert not [
            item
            for item in fake.files.values()
            if item["name"] == "変更後" and not item.get("trashed", False)
        ]
    finally:
        database.dispose()


def test_retry_accepts_remote_root_that_already_matches_local_change(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "変更前")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        root_b = fake.find_one(name="変更前", parent_id="root-b")
        with database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            assert folder is not None
            folder.name = "変更後"
            folder.normalized_name = "変更後".casefold()
            session.commit()
        fake.fail_file_update_id = str(root_b["id"])

        with pytest.raises(AppError, match="simulated Drive update failure"):
            drive.mirror_library_folder(folder_id)

        assert fake.find_one(name="変更後", parent_id="root-a")
        assert fake.find_one(name="変更前", parent_id="root-b")

        drive.mirror_library_folder(folder_id)

        assert fake.find_one(name="変更後", parent_id="root-a")
        assert fake.find_one(name="変更後", parent_id="root-b")
    finally:
        database.dispose()


def test_local_folder_move_updates_every_drive_mirror(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        first_parent_id = _create_local_folder(database, "移動元")
        second_parent_id = _create_local_folder(database, "移動先")
        child_id = _create_local_folder(database, "子", parent_id=first_parent_id)
        drive = _drive_service(database, fake, monkeypatch)
        for folder_id in (first_parent_id, second_parent_id, child_id):
            drive.mirror_library_folder(folder_id)

        with database.session() as session:
            child = session.get(DocumentFolder, child_id)
            assert child is not None
            child.parent_id = second_parent_id
            session.commit()

        drive.mirror_library_folder(child_id)

        for root_id in ("root-a", "root-b"):
            target_parent = fake.find_one(name="移動先", parent_id=root_id)
            assert fake.find_one(name="子", parent_id=str(target_parent["id"]))
            source_parent = fake.find_one(name="移動元", parent_id=root_id)
            assert not [
                item
                for item in fake.files.values()
                if item["name"] == "子"
                and source_parent["id"] in item.get("parents", [])
                and not item.get("trashed", False)
            ]
    finally:
        database.dispose()


def test_local_empty_folder_delete_trashes_all_mirrors(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        library = DocumentLibraryService(database, google_drive=drive)
        created = library.create_folder(name="削除対象", parent_id=None)
        remote_ids = {
            str(fake.find_one(name="削除対象", parent_id=root_id)["id"])
            for root_id in ("root-a", "root-b")
        }

        library.delete_folder(created["id"])

        with database.session() as session:
            assert session.get(DocumentFolder, created["id"]) is None
        assert all(fake.files[file_id]["trashed"] is True for file_id in remote_ids)
    finally:
        database.dispose()


def test_local_folder_delete_with_untracked_drive_child_preserves_both_sides(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        library = DocumentLibraryService(database, google_drive=drive)
        created = library.create_folder(name="削除禁止", parent_id=None)
        remote = fake.find_one(name="削除禁止", parent_id="root-a")
        fake.add_spreadsheet("untracked-sheet", "未登録", parents=[str(remote["id"])])

        with pytest.raises(AppError) as raised:
            library.delete_folder(created["id"])

        assert raised.value.code == "DRIVE_FOLDER_NOT_EMPTY"
        with database.session() as session:
            assert session.get(DocumentFolder, created["id"]) is not None
        assert fake.files[str(remote["id"])]["trashed"] is False
        assert fake.files["untracked-sheet"]["trashed"] is False
    finally:
        database.dispose()


def test_local_folder_delete_rejects_unmerged_drive_rename_or_move(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        library = DocumentLibraryService(database, google_drive=drive)
        created = library.create_folder(name="変更競合", parent_id=None)
        remote = fake.find_one(name="変更競合", parent_id="root-a")
        fake.add_folder(
            "outside-root",
            "同期対象外",
            parents=[],
            record_change=False,
        )
        fake.external_update(
            str(remote["id"]),
            name="Driveで改名済み",
            parents=["outside-root"],
        )

        with pytest.raises(AppError) as raised:
            library.delete_folder(created["id"])

        assert raised.value.code == "DOCUMENT_FOLDER_SYNC_CONFLICT"
        with database.session() as session:
            assert session.get(DocumentFolder, created["id"]) is not None
        assert fake.files[str(remote["id"])]["trashed"] is False
    finally:
        database.dispose()


def test_drive_folder_rename_and_move_updates_local_and_other_root(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        parent_id = _create_local_folder(database, "親")
        child_id = _create_local_folder(database, "変更前", parent_id=parent_id)
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(parent_id)
        drive.mirror_library_folder(child_id)
        drive.sync_document_library()

        remote_parent_a = fake.find_one(name="親", parent_id="root-a")
        remote_child_a = fake.find_one(name="変更前", parent_id=str(remote_parent_a["id"]))
        fake.external_update(str(remote_child_a["id"]), name="Driveで変更", parents=["root-a"])

        drive.sync_document_library()

        with database.session() as session:
            child = session.get(DocumentFolder, child_id)
            assert child is not None
            assert child.name == "Driveで変更"
            assert child.normalized_name == "driveで変更"
            assert child.parent_id is None
        assert fake.find_one(name="Driveで変更", parent_id="root-b")
    finally:
        database.dispose()


def test_drive_move_of_known_spreadsheet_updates_local_location(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        local_folder_id = _create_local_folder(database, "保管先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(local_folder_id)
        remote_folder = fake.find_one(name="保管先", parent_id="root-a")
        fake.add_spreadsheet("sheet-1", "書類", parents=["root-a"], record_change=False)
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-1",
            drive_parent_id="root-a",
        )
        drive.sync_document_library()

        fake.external_update("sheet-1", parents=[str(remote_folder["id"])])
        drive.sync_document_library()

        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id == local_folder_id
            assert document.library_location_version == 2
            assert document.drive_folder_id == remote_folder["id"]
    finally:
        database.dispose()


def test_unchanged_second_sync_uses_changes_without_full_tree_scan(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        assert drive.sync_document_library()["status"] == "SYNCED"
        fake.calls.clear()

        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        assert [call for call in fake.calls if call[1].endswith("/changes")]
        assert not [
            call
            for call in fake.calls
            if call[0] == "GET" and call[1].endswith("/files")
        ]
    finally:
        database.dispose()


def test_unrelated_drive_change_advances_token_without_full_tree_scan(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        assert drive.sync_document_library()["status"] == "SYNCED"
        fake.add_folder(
            "outside-root",
            "同期対象外",
            parents=[],
            record_change=False,
        )
        fake.add_folder("outside-child", "対象外変更", parents=["outside-root"])
        fake.calls.clear()

        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        assert not [
            call
            for call in fake.calls
            if call[0] == "GET" and call[1].endswith("/files")
        ]
    finally:
        database.dispose()


def test_restored_ownerless_binding_forces_full_sync_without_drive_change(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        assert drive.sync_document_library()["status"] == "SYNCED"
        fake.add_spreadsheet(
            "sheet-restored-ownerless",
            "補完された旧書類",
            parents=["root-a"],
            record_change=False,
        )
        _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-restored-ownerless",
            drive_parent_id="root-a",
            drive_google_subject=None,
        )
        fake.calls.clear()

        outcome = drive.sync_document_library()

        assert outcome["status"] == "PARTIAL"
        assert [item["code"] for item in outcome["conflictItems"]] == [
            "DRIVE_DOCUMENT_OWNER_UNCONFIRMED"
        ]
        assert [
            call
            for call in fake.calls
            if call[0] == "GET" and call[1].endswith("/files")
        ]
    finally:
        database.dispose()


def test_relevant_drive_change_runs_full_sync_and_refreshes_token(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        assert drive.sync_document_library()["status"] == "SYNCED"
        fake.add_folder("external-incremental", "差分取込", parents=["root-a"])
        fake.calls.clear()

        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        assert [
            call
            for call in fake.calls
            if call[0] == "GET" and call[1].endswith("/files")
        ]
        with database.session() as session:
            assert session.query(DocumentFolder).filter_by(name="差分取込").one()
            state = session.get(DocumentLibrarySyncState, "subject-1")
            assert state is not None
            assert state.drive_change_token == str(len(fake._changes))
    finally:
        database.dispose()


def test_invalid_change_token_falls_back_to_full_sync(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        assert drive.sync_document_library()["status"] == "SYNCED"
        fake.fail_next_change_list_invalid = True
        fake.calls.clear()

        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        assert [
            call
            for call in fake.calls
            if call[0] == "GET" and call[1].endswith("/files")
        ]
        with database.session() as session:
            state = session.get(DocumentLibrarySyncState, "subject-1")
            assert state is not None
            assert state.drive_change_token == str(len(fake._changes))
    finally:
        database.dispose()


def test_external_drive_folder_is_imported_and_mirrored_to_other_roots(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        drive = _drive_service(database, fake, monkeypatch)
        drive.sync_document_library()

        fake.add_folder("external-1", "Driveで作成", parents=["root-a"])
        drive.sync_document_library()

        with database.session() as session:
            imported = session.query(DocumentFolder).filter_by(name="Driveで作成").one()
            assert imported.parent_id is None
        assert fake.find_one(name="Driveで作成", parent_id="root-b")
    finally:
        database.dispose()


def test_drive_import_intent_survives_later_tag_failure_and_resumes(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        fake.add_folder("external-first", "先に取込", parents=["root-a"])
        fake.add_folder("external-second", "後で失敗", parents=["root-a"])
        fake.fail_file_update_id = "external-second"
        drive = _drive_service(database, fake, monkeypatch)

        first_attempt = drive.sync_document_library()

        assert first_attempt["status"] == "ERROR"
        with database.session() as session:
            first_folder = session.query(DocumentFolder).filter_by(name="先に取込").one()
            assert session.scalar(
                select(DocumentFolderDriveMapping.id).where(
                    DocumentFolderDriveMapping.document_folder_id == first_folder.id,
                    DocumentFolderDriveMapping.drive_folder_id == "external-first",
                )
            ) is not None
        assert fake.files["external-first"]["appProperties"][
            "llmOcrLibraryFolderId"
        ] == first_folder.id

        second_attempt = drive.sync_document_library()

        assert second_attempt["status"] == "SYNCED"
        with database.session() as session:
            assert session.query(DocumentFolder).filter_by(name="先に取込").count() == 1
            assert session.query(DocumentFolder).filter_by(name="後で失敗").count() == 1
    finally:
        database.dispose()


def test_nested_configured_root_is_not_imported_as_a_library_folder(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        fake.files["root-b"]["parents"] = ["root-a"]
        fake.add_folder("nested-external", "配下で作成", parents=["root-b"])
        drive = _drive_service(database, fake, monkeypatch)

        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        with database.session() as session:
            names = {folder.name for folder in session.query(DocumentFolder).all()}
        assert "テンプレート別保存先" not in names
        assert "配下で作成" in names
        assert fake.find_one(name="配下で作成", parent_id="root-a")
    finally:
        database.dispose()


def test_tagged_drive_folder_deleted_locally_is_not_resurrected(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        orphan = fake.add_folder("old-mirror", "削除済み", parents=["root-a"])
        orphan["appProperties"] = {
            "llmOcrLibraryFolderId": "deleted-local-folder",
            "llmOcrDriveRootId": "root-a",
        }
        drive = _drive_service(database, fake, monkeypatch)

        outcome = drive.sync_document_library()

        assert outcome["status"] == "PARTIAL"
        assert outcome["conflictItems"][0]["code"] == "LOCAL_FOLDER_REMOVED"
        with database.session() as session:
            assert session.query(DocumentFolder).count() == 0
    finally:
        database.dispose()


def test_external_delete_of_nonempty_folder_never_deletes_local_contents(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "重要書類")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        remote_folder = fake.find_one(name="重要書類", parent_id="root-a")
        fake.add_spreadsheet(
            "sheet-important",
            "重要書類",
            parents=[str(remote_folder["id"])],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-important",
            drive_parent_id=str(remote_folder["id"]),
            library_folder_id=folder_id,
        )
        drive.sync_document_library()

        fake.external_remove(str(remote_folder["id"]))
        outcome = drive.sync_document_library()

        with database.session() as session:
            assert session.get(DocumentFolder, folder_id) is not None
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id == folder_id
            assert document.library_deleted_at is None
        recreated = [
            item
            for item in fake.files.values()
            if item["name"] == "重要書類"
            and "root-a" in item.get("parents", [])
            and not item.get("trashed", False)
        ]
        assert len(recreated) == 1
        assert outcome["status"] == "PARTIAL"
        assert fake.files["sheet-important"]["parents"] == [recreated[0]["id"]]
        assert fake.files["sheet-important"]["trashed"] is False
    finally:
        database.dispose()


def test_recreated_mirror_is_adopted_after_later_root_create_failure(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "再作成対象")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        old_root_a = fake.find_one(name="再作成対象", parent_id="root-a")
        old_root_b = fake.find_one(name="再作成対象", parent_id="root-b")
        fake.external_remove(str(old_root_a["id"]))
        fake.external_remove(str(old_root_b["id"]))
        fake.fail_file_create_parent_id = "root-b"

        first_attempt = drive.sync_document_library()

        assert first_attempt["status"] == "ERROR"
        replacement_a = fake.find_one(name="再作成対象", parent_id="root-a")

        second_attempt = drive.sync_document_library()

        active_root_a = [
            item
            for item in fake.files.values()
            if item["name"] == "再作成対象"
            and "root-a" in item.get("parents", [])
            and not item.get("trashed", False)
        ]
        assert [item["id"] for item in active_root_a] == [replacement_a["id"]]
        assert fake.find_one(name="再作成対象", parent_id="root-b")
        assert second_attempt["status"] in {"PARTIAL", "SYNCED"}
        assert drive.sync_document_library()["status"] == "SYNCED"
    finally:
        database.dispose()


def test_implicit_trash_is_repaired_from_persisted_current_mapping(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "復元先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        old_folder = fake.find_one(name="復元先", parent_id="root-a")
        fake.add_spreadsheet(
            "sheet-implicit-trash",
            "復元対象",
            parents=[str(old_folder["id"])],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-implicit-trash",
            drive_parent_id=str(old_folder["id"]),
            library_folder_id=folder_id,
        )
        replacement = fake.add_folder(
            "replacement-root-a",
            "復元先",
            parents=["root-a"],
            record_change=False,
        )
        replacement["appProperties"] = {
            "llmOcrLibraryFolderId": folder_id,
            "llmOcrDriveRootId": "root-a",
        }
        fake.external_remove(str(old_folder["id"]))
        with database.session() as session:
            mapping = session.scalar(
                select(DocumentFolderDriveMapping).where(
                    DocumentFolderDriveMapping.document_folder_id == folder_id,
                    DocumentFolderDriveMapping.drive_root_folder_id == "root-a",
                )
            )
            assert mapping is not None
            mapping.drive_folder_id = str(replacement["id"])
            mapping.drive_name = "復元先"
            mapping.drive_parent_folder_id = "root-a"
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            document.drive_google_subject = "subject-1"
            document.drive_root_folder_id = "root-a"
            session.commit()

        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        assert fake.files["sheet-implicit-trash"]["parents"] == [replacement["id"]]
        assert fake.files["sheet-implicit-trash"]["trashed"] is False
    finally:
        database.dispose()


def test_failed_local_document_move_is_retryable_without_db_divergence(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "移動先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        remote_folder = fake.find_one(name="移動先", parent_id="root-a")
        fake.add_spreadsheet("sheet-move", "移動対象", parents=["root-a"], record_change=False)
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-move",
            drive_parent_id="root-a",
        )
        library = DocumentLibraryService(database, google_drive=drive)

        fake.fail_next_file_update = True
        with pytest.raises(AppError, match="simulated Drive update failure"):
            library.move_document(document_id, folder_id=folder_id, expected_version=1)

        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id is None
            assert document.library_location_version == 1
            assert document.drive_folder_id == "root-a"
        assert fake.files["sheet-move"]["parents"] == ["root-a"]

        moved = library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert moved["folderId"] == folder_id
        assert moved["locationVersion"] == 2
        assert fake.files["sheet-move"]["parents"] == [remote_folder["id"]]
    finally:
        database.dispose()


def test_document_move_commit_failure_restores_drive_and_is_retryable(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "DB失敗時の移動先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        remote_folder = fake.find_one(name="DB失敗時の移動先", parent_id="root-a")
        fake.add_spreadsheet(
            "sheet-commit-failure",
            "DB保存失敗対象",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-commit-failure",
            drive_parent_id="root-a",
        )
        library = DocumentLibraryService(database, google_drive=drive)

        original_commit = Session.commit
        fail_next_commit = True

        def commit_with_one_failure(session: Session) -> None:
            nonlocal fail_next_commit
            if fail_next_commit:
                fail_next_commit = False
                raise RuntimeError("simulated database commit failure")
            original_commit(session)

        monkeypatch.setattr(Session, "commit", commit_with_one_failure)

        with pytest.raises(AppError) as caught:
            library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert caught.value.code == "DRIVE_DOCUMENT_MOVE_ROLLED_BACK"
        assert fake.files["sheet-commit-failure"]["parents"] == ["root-a"]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id is None
            assert document.library_location_version == 1
            assert document.drive_folder_id == "root-a"

        moved = library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert moved["folderId"] == folder_id
        assert moved["locationVersion"] == 2
        assert fake.files["sheet-commit-failure"]["parents"] == [remote_folder["id"]]
    finally:
        database.dispose()


def test_document_move_response_loss_is_compensated_before_retry(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "応答消失時の移動先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        remote_folder = fake.find_one(name="応答消失時の移動先", parent_id="root-a")
        fake.add_spreadsheet(
            "sheet-move-response-loss",
            "応答消失対象",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-move-response-loss",
            drive_parent_id="root-a",
        )
        library = DocumentLibraryService(database, google_drive=drive)

        fake.fail_file_update_after_apply_id = "sheet-move-response-loss"
        with pytest.raises(AppError, match="simulated Drive update failure after apply"):
            library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert fake.files["sheet-move-response-loss"]["parents"] == ["root-a"]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id is None
            assert document.library_location_version == 1
            assert document.drive_folder_id == "root-a"

        moved = library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert moved["folderId"] == folder_id
        assert moved["locationVersion"] == 2
        assert fake.files["sheet-move-response-loss"]["parents"] == [
            remote_folder["id"]
        ]
    finally:
        database.dispose()


def test_document_move_commit_failure_removes_new_remote_target_folder(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "未同期の移動先")
        drive = _drive_service(database, fake, monkeypatch)
        fake.add_spreadsheet(
            "sheet-new-target-rollback",
            "新規移動先補償対象",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-new-target-rollback",
            drive_parent_id="root-a",
        )
        library = DocumentLibraryService(database, google_drive=drive)

        original_commit = Session.commit
        fail_next_commit = True

        def commit_with_one_failure(session: Session) -> None:
            nonlocal fail_next_commit
            if fail_next_commit:
                fail_next_commit = False
                raise RuntimeError("simulated database commit failure")
            original_commit(session)

        monkeypatch.setattr(Session, "commit", commit_with_one_failure)

        with pytest.raises(AppError) as caught:
            library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert caught.value.code == "DRIVE_DOCUMENT_MOVE_ROLLED_BACK"
        assert fake.files["sheet-new-target-rollback"]["parents"] == ["root-a"]
        remote_folder = fake.find_one(
            name="未同期の移動先",
            parent_id="root-a",
            include_trashed=True,
        )
        assert remote_folder["trashed"] is True
        with database.session() as session:
            assert session.scalar(
                select(DocumentFolderDriveMapping).where(
                    DocumentFolderDriveMapping.document_folder_id == folder_id,
                    DocumentFolderDriveMapping.drive_root_folder_id == "root-a",
                )
            ) is None
    finally:
        database.dispose()


def test_document_move_reports_partial_when_compensation_fails(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "補償失敗時の移動先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        remote_folder = fake.find_one(name="補償失敗時の移動先", parent_id="root-a")
        fake.add_spreadsheet(
            "sheet-compensation-failure",
            "補償失敗対象",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-compensation-failure",
            drive_parent_id="root-a",
        )
        library = DocumentLibraryService(database, google_drive=drive)

        original_commit = Session.commit
        fail_next_commit = True

        def commit_with_one_failure(session: Session) -> None:
            nonlocal fail_next_commit
            if fail_next_commit:
                fail_next_commit = False
                raise RuntimeError("simulated database commit failure")
            original_commit(session)

        original_move = drive._move_drive_item
        move_calls = 0

        def fail_compensation_move(
            file_id: str,
            parent_id: str,
            current_parents: list[str],
        ) -> dict[str, Any]:
            nonlocal move_calls
            move_calls += 1
            if move_calls == 2:
                raise RuntimeError("simulated compensation failure")
            return original_move(file_id, parent_id, current_parents)

        monkeypatch.setattr(Session, "commit", commit_with_one_failure)
        monkeypatch.setattr(drive, "_move_drive_item", fail_compensation_move)

        with pytest.raises(AppError) as caught:
            library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert caught.value.code == "DRIVE_DOCUMENT_MOVE_PARTIAL"
        assert fake.files["sheet-compensation-failure"]["parents"] == [
            remote_folder["id"]
        ]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id is None
            assert document.library_location_version == 1
            assert document.drive_folder_id == "root-a"
    finally:
        database.dispose()


def test_document_move_detects_remote_change_after_patch_and_restores_parent(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "競合検出先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        fake.add_spreadsheet(
            "sheet-post-move-conflict",
            "移動前名",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-post-move-conflict",
            drive_parent_id="root-a",
        )
        library = DocumentLibraryService(database, google_drive=drive)
        original_request = fake.request
        injected = False

        def request_with_concurrent_rename(
            method: str,
            url: str,
            **kwargs: object,
        ) -> dict[str, Any]:
            nonlocal injected
            result = original_request(method, url, **kwargs)
            params = kwargs.get("params")
            query = params if isinstance(params, dict) else {}
            if (
                not injected
                and method == "PATCH"
                and url.endswith("/files/sheet-post-move-conflict")
                and query.get("addParents")
            ):
                injected = True
                fake.files["sheet-post-move-conflict"]["name"] = "外部で変更された名前"
            return result

        monkeypatch.setattr(drive, "_request", request_with_concurrent_rename)

        with pytest.raises(AppError) as caught:
            library.move_document(document_id, folder_id=folder_id, expected_version=1)

        assert caught.value.code == "DRIVE_ITEM_MOVE_CONFLICT"
        assert fake.files["sheet-post-move-conflict"]["parents"] == ["root-a"]
        assert fake.files["sheet-post-move-conflict"]["name"] == "外部で変更された名前"
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id is None
            assert document.library_location_version == 1
            assert document.drive_folder_id == "root-a"
    finally:
        database.dispose()


def test_spreadsheet_rename_detects_remote_property_change_after_patch(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        fake.add_spreadsheet(
            "sheet-post-rename-conflict",
            "変更前",
            parents=["root-a"],
            record_change=False,
        )
        drive = _drive_service(database, fake, monkeypatch)
        original_request = fake.request
        injected = False

        def request_with_concurrent_property_change(
            method: str,
            url: str,
            **kwargs: object,
        ) -> dict[str, Any]:
            nonlocal injected
            result = original_request(method, url, **kwargs)
            body = kwargs.get("json")
            payload = body if isinstance(body, dict) else {}
            if (
                not injected
                and method == "PATCH"
                and url.endswith("/files/sheet-post-rename-conflict")
                and payload.get("name") == "変更後"
            ):
                injected = True
                fake.files["sheet-post-rename-conflict"]["appProperties"] = {
                    "externalChange": "detected"
                }
            return result

        monkeypatch.setattr(drive, "_request", request_with_concurrent_property_change)

        with pytest.raises(AppError) as caught:
            drive.rename_spreadsheet(
                "sheet-post-rename-conflict",
                "変更後",
                baseline_name="変更前",
            )

        assert caught.value.code == "DRIVE_DOCUMENT_NAME_CONFLICT"
        assert fake.files["sheet-post-rename-conflict"]["name"] == "変更後"
        assert fake.files["sheet-post-rename-conflict"]["appProperties"] == {
            "externalChange": "detected"
        }
    finally:
        database.dispose()


def test_document_move_detects_concurrent_local_location_update(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        target_folder_id = _create_local_folder(database, "最初の移動先")
        concurrent_folder_id = _create_local_folder(database, "同時更新先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(target_folder_id)
        fake.add_spreadsheet(
            "sheet-local-location-conflict",
            "ローカル競合対象",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-local-location-conflict",
            drive_parent_id="root-a",
        )
        library = DocumentLibraryService(database, google_drive=drive)
        original_move = drive._move_drive_item
        injected = False

        def move_with_concurrent_local_update(
            file_id: str,
            parent_id: str,
            current_parents: list[str],
        ) -> dict[str, Any]:
            nonlocal injected
            result = original_move(file_id, parent_id, current_parents)
            if not injected:
                injected = True
                with database.session() as concurrent_session:
                    document = concurrent_session.get(LedgerDocument, document_id)
                    assert document is not None
                    document.library_folder_id = concurrent_folder_id
                    document.library_location_version += 1
                    concurrent_session.commit()
            return result

        monkeypatch.setattr(drive, "_move_drive_item", move_with_concurrent_local_update)

        with pytest.raises(AppError) as caught:
            library.move_document(
                document_id,
                folder_id=target_folder_id,
                expected_version=1,
            )

        assert caught.value.code == "DOCUMENT_LOCATION_CONFLICT"
        assert fake.files["sheet-local-location-conflict"]["parents"] == ["root-a"]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id == concurrent_folder_id
            assert document.library_location_version == 2
            assert document.drive_folder_id == "root-a"
    finally:
        database.dispose()


def test_explicitly_trashed_spreadsheet_is_not_restored_and_reports_partial(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        fake.add_spreadsheet(
            "sheet-explicit-trash",
            "ゴミ箱へ移動する書類",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-explicit-trash",
            drive_parent_id="root-a",
        )
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            document.drive_google_subject = "subject-1"
            document.drive_root_folder_id = "root-a"
            document.document_name = "ゴミ箱へ移動する書類"
            document.library_name = "ゴミ箱へ移動する書類"
            session.commit()
        drive = _drive_service(database, fake, monkeypatch)
        assert drive.sync_document_library()["status"] == "SYNCED"

        fake.external_remove("sheet-explicit-trash")
        call_start = len(fake.calls)
        outcome = drive.sync_document_library()

        assert outcome["status"] == "PARTIAL"
        assert outcome["conflicts"] == 1
        assert outcome["conflictItems"][0]["code"] == (
            "DRIVE_SPREADSHEET_EXPLICITLY_TRASHED"
        )
        assert fake.files["sheet-explicit-trash"]["trashed"] is True
        assert fake.files["sheet-explicit-trash"]["explicitlyTrashed"] is True
        post_trash_calls = fake.calls[call_start:]
        assert not [
            call
            for call in post_trash_calls
            if call[0] == "PATCH" and call[1].endswith("/files/sheet-explicit-trash")
        ]
    finally:
        database.dispose()


@pytest.mark.parametrize("move_under_mapped_folder", [False, True])
def test_known_spreadsheet_can_move_from_root_a_to_root_b(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    *,
    move_under_mapped_folder: bool,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        local_folder_id = _create_local_folder(database, "B側の保管先")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(local_folder_id)
        remote_folder_b = fake.find_one(name="B側の保管先", parent_id="root-b")
        destination_id = (
            str(remote_folder_b["id"]) if move_under_mapped_folder else "root-b"
        )
        fake.add_spreadsheet(
            "sheet-cross-root",
            "ルート間移動",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-cross-root",
            drive_parent_id="root-a",
        )
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            document.drive_google_subject = "subject-1"
            document.drive_root_folder_id = "root-a"
            document.document_name = "ルート間移動"
            document.library_name = "ルート間移動"
            session.commit()
        assert drive.sync_document_library()["status"] == "SYNCED"

        fake.external_update("sheet-cross-root", parents=[destination_id])
        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_google_subject == "subject-1"
            assert document.drive_root_folder_id == "root-b"
            assert document.drive_folder_id == destination_id
            assert document.library_folder_id == (
                local_folder_id if move_under_mapped_folder else None
            )
            assert document.library_location_version == (
                2 if move_under_mapped_folder else 1
            )
    finally:
        database.dispose()


def test_document_owned_by_another_google_subject_is_silently_skipped(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        fake.add_spreadsheet(
            "sheet-other-subject",
            "別アカウントの書類",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-other-subject",
            drive_parent_id="foreign-root",
        )
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            document.drive_google_subject = "subject-2"
            document.drive_root_folder_id = "foreign-root"
            document.document_name = "別アカウントの書類"
            document.library_name = "別アカウントの書類"
            session.commit()
        drive = _drive_service(database, fake, monkeypatch)

        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        assert outcome["conflicts"] == 0
        assert outcome["conflictItems"] == []
        assert not [
            call
            for call in fake.calls
            if call[0] in {"GET", "PATCH"}
            and call[1].endswith("/files/sheet-other-subject")
        ]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_google_subject == "subject-2"
            assert document.drive_root_folder_id == "foreign-root"
            assert document.drive_folder_id == "foreign-root"
        assert drive.spreadsheet_targets() == []
        for operation in (
            lambda: drive.document_target(document_id),
            lambda: drive.create_document_spreadsheet(document_id, "変更しない"),
            lambda: drive.export_spreadsheet_pdf("sheet-other-subject"),
        ):
            with pytest.raises(AppError) as caught:
                operation()
            assert caught.value.code == "DRIVE_DOCUMENT_ACCOUNT_MISMATCH"
    finally:
        database.dispose()


def test_ownerless_document_in_shared_root_remains_unclaimed(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        with database.session() as session:
            session.add(
                GoogleOAuthAccount(
                    google_subject="subject-2",
                    email="other@example.com",
                    encrypted_refresh_token="encrypted",
                    scopes="https://www.googleapis.com/auth/drive.file",
                    default_output_folder_id="root-a",
                    default_output_folder_name="共通保存先",
                    default_output_folder_path="共通保存先",
                )
            )
            session.commit()
        fake.add_spreadsheet(
            "sheet-shared-root",
            "共有ルートの書類",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-shared-root",
            drive_parent_id="root-a",
            drive_google_subject=None,
        )
        drive = _drive_service(database, fake, monkeypatch)

        outcome = drive.sync_document_library()

        assert outcome["status"] == "PARTIAL"
        assert [item["code"] for item in outcome["conflictItems"]] == [
            "DRIVE_DOCUMENT_OWNER_UNCONFIRMED"
        ]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_google_subject is None
        with pytest.raises(AppError) as caught:
            drive.rename_spreadsheet("sheet-shared-root", "変更しない")
        assert caught.value.code == "DRIVE_DOCUMENT_ACCOUNT_AMBIGUOUS"
        assert fake.files["sheet-shared-root"]["name"] == "共有ルートの書類"
    finally:
        database.dispose()


def test_ownerless_document_can_be_explicitly_confirmed_for_current_account(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        fake.add_spreadsheet(
            "sheet-legacy-confirm",
            "旧書類",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-legacy-confirm",
            drive_parent_id="root-a",
            drive_google_subject=None,
        )
        fake.files["sheet-legacy-confirm"]["appProperties"] = {
            "llmOcrLedgerDocumentId": document_id,
        }
        drive = _drive_service(database, fake, monkeypatch)

        claimed = drive.confirm_legacy_document_ownership(document_id)

        assert claimed["googleSubject"] == "subject-1"
        assert claimed["driveRootFolderId"] == "root-a"
        assert claimed["alreadyConfirmed"] is False
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_google_subject == "subject-1"
            assert document.drive_ownership_confirmed_at is not None
            assert document.drive_ownership_confirmation_source == "USER_CONFIRMED"
    finally:
        database.dispose()


def test_owner_confirmation_nests_root_sheet_in_existing_library_folder(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "確定済み")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        remote_folder = fake.find_one(name="確定済み", parent_id="root-a")
        fake.add_spreadsheet(
            "sheet-legacy-root",
            "旧確定結果",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-legacy-root",
            drive_parent_id="root-a",
            drive_google_subject=None,
            library_folder_id=folder_id,
        )

        claimed = drive.confirm_legacy_document_ownership(document_id)

        assert claimed["locationAlignedFromLibrary"] is True
        assert fake.files["sheet-legacy-root"]["parents"] == [remote_folder["id"]]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_google_subject == "subject-1"
            assert document.library_folder_id == folder_id
            assert document.drive_folder_id == remote_folder["id"]
    finally:
        database.dispose()


def test_owner_confirmation_keeps_existing_mapped_drive_folder_authoritative(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        local_folder_id = _create_local_folder(database, "独自側")
        drive_folder_id = _create_local_folder(database, "Drive側")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(local_folder_id)
        drive.mirror_library_folder(drive_folder_id)
        remote_drive_folder = fake.find_one(name="Drive側", parent_id="root-a")
        fake.add_spreadsheet(
            "sheet-legacy-mapped",
            "移動済み旧書類",
            parents=[str(remote_drive_folder["id"])],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-legacy-mapped",
            drive_parent_id=str(remote_drive_folder["id"]),
            drive_google_subject=None,
            library_folder_id=local_folder_id,
        )

        claimed = drive.confirm_legacy_document_ownership(document_id)

        assert claimed["locationAlignedFromLibrary"] is False
        assert fake.files["sheet-legacy-mapped"]["parents"] == [
            remote_drive_folder["id"]
        ]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id == drive_folder_id
            assert document.drive_folder_id == remote_drive_folder["id"]
    finally:
        database.dispose()


def test_owner_confirmation_rejects_spreadsheet_tagged_for_another_ledger(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        fake.add_spreadsheet(
            "sheet-wrong-ledger",
            "別書類",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-wrong-ledger",
            drive_parent_id="root-a",
            drive_google_subject=None,
        )
        fake.files["sheet-wrong-ledger"]["appProperties"] = {
            "llmOcrLedgerDocumentId": "another-document",
        }
        drive = _drive_service(database, fake, monkeypatch)

        with pytest.raises(AppError) as caught:
            drive.confirm_legacy_document_ownership(document_id)

        assert caught.value.code == "DRIVE_DOCUMENT_IDENTITY_CONFLICT"
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_google_subject is None
    finally:
        database.dispose()


def test_ownerless_document_uses_actual_shared_root_not_stale_private_root(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        with database.session() as session:
            session.add_all(
                (
                    GoogleOAuthAccount(
                        google_subject="subject-2",
                        email="other@example.com",
                        encrypted_refresh_token="encrypted",
                        scopes="https://www.googleapis.com/auth/drive.file",
                        default_output_folder_id="foreign-root",
                    ),
                    TemplateOutputFolder(
                        google_subject="subject-1",
                        template_id=template_id,
                        folder_id="root-b",
                        folder_name="共有保存先",
                        folder_path="共有保存先",
                    ),
                )
            )
            session.commit()
            session.add(
                TemplateOutputFolder(
                    google_subject="subject-2",
                    template_id=template_id,
                    folder_id="root-b",
                    folder_name="共有保存先",
                    folder_path="共有保存先",
                )
            )
            session.commit()
        fake.add_spreadsheet(
            "sheet-stale-private-root",
            "共有ルートの書類",
            parents=["root-b"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-stale-private-root",
            drive_parent_id="root-b",
            drive_google_subject=None,
        )
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            document.drive_root_folder_id = "root-a"
            session.commit()
        drive = _drive_service(database, fake, monkeypatch)

        outcome = drive.sync_document_library()

        assert outcome["status"] == "PARTIAL"
        assert [item["code"] for item in outcome["conflictItems"]] == [
            "DRIVE_DOCUMENT_OWNER_UNCONFIRMED"
        ]
        assert outcome["conflictItems"][0]["driveRootId"] == "root-a"
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.drive_google_subject is None
            assert document.drive_root_folder_id == "root-a"
    finally:
        database.dispose()


def test_drive_rename_updates_confirmed_name_without_duplicate_xlsx_suffix(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        fake.add_spreadsheet(
            "sheet-confirmed-name",
            "変更前.xlsx",
            parents=["root-a"],
            record_change=False,
        )
        document_id = _seed_document(
            database,
            template_id=template_id,
            spreadsheet_id="sheet-confirmed-name",
            drive_parent_id="root-a",
        )
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            document.drive_google_subject = "subject-1"
            document.drive_root_folder_id = "root-a"
            document.document_name = "変更前"
            document.library_name = "変更前"
            session.commit()
        _attach_confirmed_result(
            database,
            document_id=document_id,
            spreadsheet_id="sheet-confirmed-name",
            spreadsheet_name="変更前",
        )
        drive = _drive_service(database, fake, monkeypatch)
        assert drive.sync_document_library()["status"] == "SYNCED"

        fake.external_update("sheet-confirmed-name", name="変更後.xlsx")
        outcome = drive.sync_document_library()

        assert outcome["status"] == "SYNCED"
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            confirmed = (
                session.query(ConfirmedResult)
                .filter_by(spreadsheet_id="sheet-confirmed-name")
                .one()
            )
            assert document is not None
            assert document.document_name == "変更後"
            assert document.library_name == "変更後"
            assert confirmed.spreadsheet_name == "変更後"
            assert f"{confirmed.spreadsheet_name}.xlsx" == "変更後.xlsx"
    finally:
        database.dispose()


def test_missing_db_mapping_adopts_tagged_drive_folder_without_duplicate_post(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "タグで再接続")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        original_ids = {
            root_id: str(fake.find_one(name="タグで再接続", parent_id=root_id)["id"])
            for root_id in ("root-a", "root-b")
        }
        with database.session() as session:
            deleted = session.query(DocumentFolderDriveMapping).delete()
            assert deleted == 2
            session.commit()
        fake.calls.clear()

        outcome = drive.mirror_library_folder(folder_id)

        assert outcome == {"mirrored": 0, "rootCount": 2}
        assert not [call for call in fake.calls if call[0] == "POST"]
        with database.session() as session:
            mappings = session.query(DocumentFolderDriveMapping).all()
            assert len(mappings) == 2
            assert {
                mapping.drive_root_folder_id: mapping.drive_folder_id
                for mapping in mappings
            } == original_ids
    finally:
        database.dispose()


def test_document_sheet_create_retries_after_pre_create_failure_without_duplicate(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        document_id, job_id = _seed_unbound_document(
            database,
            template_id=template_id,
        )
        drive = _drive_service(database, fake, monkeypatch)

        fake.fail_next_file_create = True
        with pytest.raises(AppError, match="simulated Drive create failure"):
            drive.create_document_spreadsheet(
                document_id,
                "OCR",
                source_job_id=job_id,
                source_revision=1,
            )

        recovered = drive.create_document_spreadsheet(
            document_id,
            "OCR",
            source_job_id=job_id,
            source_revision=1,
        )
        operation_id = f"document-create:{document_id}"
        operation_files = [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get(
                "llmOcrSpreadsheetOperationId"
            )
            == operation_id
        ]
        assert len(operation_files) == 1
        assert recovered[0] == operation_files[0]["id"]
    finally:
        database.dispose()


def test_document_sheet_create_recovers_after_response_loss_without_duplicate(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        document_id, job_id = _seed_unbound_document(
            database,
            template_id=template_id,
            revision=3,
        )
        drive = _drive_service(database, fake, monkeypatch)

        fake.fail_next_file_create_after_apply = True
        with pytest.raises(AppError, match="simulated file create response loss"):
            drive.create_document_spreadsheet(
                document_id,
                "OCR",
                source_job_id=job_id,
                source_revision=3,
            )

        recovered = drive.create_document_spreadsheet(
            document_id,
            "OCR",
            source_job_id=job_id,
            source_revision=3,
        )
        operation_id = f"document-create:{document_id}"
        operation_files = [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get(
                "llmOcrSpreadsheetOperationId"
            )
            == operation_id
        ]
        assert len(operation_files) == 1
        assert recovered[0] == operation_files[0]["id"]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            intent = session.get(SpreadsheetOutputOperation, operation_id)
            assert document is not None
            assert intent is not None
            assert document.spreadsheet_id == recovered[0]
            assert intent.spreadsheet_id == recovered[0]
            assert intent.source_job_id == job_id
            assert intent.source_revision == 3
    finally:
        database.dispose()


def test_document_sheet_create_recovers_after_binding_commit_failure(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        document_id, job_id = _seed_unbound_document(
            database,
            template_id=template_id,
        )
        drive = _drive_service(database, fake, monkeypatch)
        original_commit = Session.commit
        fail_binding_commit = True

        def commit_with_binding_failure(session: Session) -> None:
            nonlocal fail_binding_commit
            binding_pending = any(
                isinstance(item, LedgerDocument)
                and item.id == document_id
                and item.spreadsheet_id is not None
                for item in session.dirty
            )
            if fail_binding_commit and binding_pending:
                fail_binding_commit = False
                raise RuntimeError("simulated spreadsheet binding commit failure")
            original_commit(session)

        monkeypatch.setattr(Session, "commit", commit_with_binding_failure)
        with pytest.raises(
            RuntimeError,
            match="simulated spreadsheet binding commit failure",
        ):
            drive.create_document_spreadsheet(
                document_id,
                "OCR",
                source_job_id=job_id,
                source_revision=1,
            )

        recovered = drive.create_document_spreadsheet(
            document_id,
            "OCR",
            source_job_id=job_id,
            source_revision=1,
        )
        operation_id = f"document-create:{document_id}"
        operation_files = [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get(
                "llmOcrSpreadsheetOperationId"
            )
            == operation_id
        ]
        assert len(operation_files) == 1
        assert recovered[0] == operation_files[0]["id"]
    finally:
        database.dispose()


@pytest.mark.parametrize(
    ("subject", "revision", "expected_code"),
    (
        ("subject-2", 1, "GOOGLE_ACCOUNT_REQUIRED"),
        ("subject-1", 2, "SPREADSHEET_OUTPUT_STALE"),
    ),
)
def test_document_sheet_create_rejects_account_or_revision_mismatch(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
    revision: int,
    expected_code: str,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        with database.session() as session:
            session.add(
                GoogleOAuthAccount(
                    google_subject="subject-2",
                    email="other@example.com",
                    encrypted_refresh_token="encrypted",
                    scopes="https://www.googleapis.com/auth/drive.file",
                    default_output_folder_id="root-a",
                    default_output_folder_name="共通保存先",
                    default_output_folder_path="共通保存先",
                )
            )
            session.commit()
        document_id, job_id = _seed_unbound_document(
            database,
            template_id=template_id,
        )
        drive = _drive_service(database, fake, monkeypatch)
        fake.fail_next_file_create_after_apply = True
        with pytest.raises(AppError, match="simulated file create response loss"):
            drive.create_document_spreadsheet(
                document_id,
                "OCR",
                source_job_id=job_id,
                source_revision=1,
            )

        with pytest.raises(AppError) as caught:
            drive.create_document_spreadsheet(
                document_id,
                "OCR",
                expected_subject=subject,
                source_job_id=job_id,
                source_revision=revision,
            )
        assert caught.value.code == expected_code
        operation_files = [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get(
                "llmOcrSpreadsheetOperationId"
            )
            == f"document-create:{document_id}"
        ]
        assert len(operation_files) == 1
    finally:
        database.dispose()


def test_xlsx_output_is_idempotent_and_preserves_owned_drive_location(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        folder_id = _create_local_folder(database, "確定済み")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        with database.session() as session:
            document = LedgerDocument(
                template_id=template_id,
                individual_id="1234567890",
                current_document_json={},
                library_folder_id=folder_id,
            )
            session.add(document)
            session.commit()
            document_id = document.id
        job_id = _attach_pending_confirmed_result(
            database,
            document_id=document_id,
        )

        fake.fail_next_upload_after_apply = True
        with pytest.raises(AppError, match="simulated upload response loss"):
            drive.create_spreadsheet_from_xlsx(
                document_id=document_id,
                template_id=template_id,
                operation_id="job-1:1",
                source_job_id=job_id,
                source_revision=1,
                name="確定結果.xlsx",
                content=b"xlsx-data",
            )

        fake.add_folder(
            "root-c",
            "新しい既定保存先",
            parents=[],
            record_change=False,
        )
        with database.session() as session:
            account = session.get(GoogleOAuthAccount, "subject-1")
            assert account is not None
            account.default_output_folder_id = "root-c"
            account.default_output_folder_name = "新しい既定保存先"
            account.default_output_folder_path = "新しい既定保存先"
            session.commit()

        first = drive.create_spreadsheet_from_xlsx(
            document_id=document_id,
            template_id=template_id,
            operation_id="job-1:1",
            source_job_id=job_id,
            source_revision=1,
            name="確定結果.xlsx",
            content=b"xlsx-data",
        )
        operation_files = [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get("llmOcrSpreadsheetOperationId")
            == "job-1:1"
        ]
        assert len(operation_files) == 1
        remote_folder = fake.find_one(name="確定済み", parent_id="root-a")
        assert operation_files[0]["parents"] == [remote_folder["id"]]

        with database.session() as session:
            confirmed = (
                session.query(ConfirmedResult)
                .filter_by(job_id=job_id)
                .one()
            )
            confirmed.ocr_result_revision = 2
            confirmed.spreadsheet_name = "確定結果_再出力"
            session.commit()

        second = drive.create_spreadsheet_from_xlsx(
            document_id=document_id,
            template_id=template_id,
            operation_id="job-1:2",
            source_job_id=job_id,
            source_revision=2,
            name="確定結果_再出力.xlsx",
            content=b"xlsx-data-2",
        )

        assert first[0] != second[0]
        assert fake.files[second[0]]["parents"] == [remote_folder["id"]]
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.spreadsheet_id == second[0]
            assert document.drive_google_subject == "subject-1"
            assert document.drive_root_folder_id == "root-a"
            assert document.drive_folder_id == remote_folder["id"]
    finally:
        database.dispose()


def test_legacy_xlsx_output_local_rename_bootstraps_intent_and_converges(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, document_id, job_id = _seed_legacy_spreadsheet_output_for_rename(
            database,
            fake,
            spreadsheet_id="legacy-sheet",
        )
        drive = _drive_service(database, fake, monkeypatch)
        operation_id = f"{job_id}:1"

        result = drive.create_spreadsheet_from_xlsx(
            document_id=document_id,
            template_id=template_id,
            operation_id=operation_id,
            source_job_id=job_id,
            source_revision=1,
            name="変更後.xlsx",
            content=b"not-uploaded",
        )

        assert result == (
            "legacy-sheet",
            "https://docs.google.com/spreadsheets/d/legacy-sheet/edit",
            "変更後",
        )
        assert fake.files["legacy-sheet"]["name"] == "変更後"
        assert fake.files["legacy-sheet"]["appProperties"] == {
            "llmOcrLedgerDocumentId": document_id,
            "llmOcrDriveRootId": "root-a",
            "llmOcrSpreadsheetOperationId": operation_id,
        }
        assert not [
            call
            for call in fake.calls
            if call[0] == "POST" and "/upload/drive/v3/files" in call[1]
        ]
        with database.session() as session:
            intent = session.get(SpreadsheetOutputOperation, operation_id)
            assert intent is not None
            assert intent.spreadsheet_id == "legacy-sheet"
            assert intent.drive_name == "変更後"
            assert intent.drive_parent_folder_id == "root-a"
    finally:
        database.dispose()


@pytest.mark.parametrize(
    "failure_mode",
    ("before_apply", "after_apply_response_loss"),
)
def test_legacy_xlsx_output_tagging_failure_retry_converges_without_duplicate(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, document_id, job_id = _seed_legacy_spreadsheet_output_for_rename(
            database,
            fake,
            spreadsheet_id=f"legacy-sheet-{failure_mode}",
        )
        spreadsheet_id = f"legacy-sheet-{failure_mode}"
        operation_id = f"{job_id}:1"
        drive = _drive_service(database, fake, monkeypatch)
        if failure_mode == "before_apply":
            fake.fail_file_update_id = spreadsheet_id
        else:
            fake.fail_file_update_after_apply_id = spreadsheet_id

        with pytest.raises(AppError) as caught:
            drive.create_spreadsheet_from_xlsx(
                document_id=document_id,
                template_id=template_id,
                operation_id=operation_id,
                source_job_id=job_id,
                source_revision=1,
                name="変更後.xlsx",
                content=b"not-uploaded",
            )

        assert caught.value.code == "GOOGLE_DRIVE_ERROR"
        with database.session() as session:
            intent = session.get(SpreadsheetOutputOperation, operation_id)
            assert intent is not None
            assert intent.spreadsheet_id == spreadsheet_id
            assert intent.drive_name == "変更前"

        sync_result = drive.sync_document_library()
        assert sync_result["status"] == "PARTIAL"
        assert any(
            conflict["code"] == "SPREADSHEET_OUTPUT_PENDING"
            for conflict in sync_result["conflictItems"]
        )
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            confirmed = session.scalar(
                select(ConfirmedResult).where(ConfirmedResult.job_id == job_id)
            )
            assert document is not None
            assert confirmed is not None
            assert document.document_name == "変更後"
            assert confirmed.spreadsheet_name == "変更後"

        result = drive.create_spreadsheet_from_xlsx(
            document_id=document_id,
            template_id=template_id,
            operation_id=operation_id,
            source_job_id=job_id,
            source_revision=1,
            name="変更後.xlsx",
            content=b"not-uploaded",
        )

        assert result[0] == spreadsheet_id
        assert result[2] == "変更後"
        matching_files = [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get("llmOcrSpreadsheetOperationId")
            == operation_id
        ]
        assert [item["id"] for item in matching_files] == [spreadsheet_id]
        assert matching_files[0]["name"] == "変更後"
        assert not [
            call
            for call in fake.calls
            if call[0] == "POST" and "/upload/drive/v3/files" in call[1]
        ]
        with database.session() as session:
            intent = session.get(SpreadsheetOutputOperation, operation_id)
            assert intent is not None
            assert intent.spreadsheet_id == spreadsheet_id
            assert intent.drive_name == "変更後"
    finally:
        database.dispose()


def test_xlsx_output_rejects_document_owned_by_another_account_before_upload(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        with database.session() as session:
            document = LedgerDocument(
                template_id=template_id,
                individual_id="1234567890",
                current_document_json={},
                drive_google_subject="subject-2",
                drive_root_folder_id="foreign-root",
            )
            session.add(document)
            session.commit()
            document_id = document.id
        drive = _drive_service(database, fake, monkeypatch)

        with pytest.raises(AppError) as caught:
            drive.create_spreadsheet_from_xlsx(
                document_id=document_id,
                template_id=template_id,
                operation_id="job-cross-account:1",
                source_job_id="job-cross-account",
                source_revision=1,
                name="作成しない.xlsx",
                content=b"xlsx-data",
            )

        assert caught.value.code == "DRIVE_DOCUMENT_ACCOUNT_MISMATCH"
        assert not [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get("llmOcrSpreadsheetOperationId")
            == "job-cross-account:1"
        ]
    finally:
        database.dispose()


def test_xlsx_output_does_not_bind_when_revision_changes_during_upload(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        with database.session() as session:
            document = LedgerDocument(
                template_id=template_id,
                individual_id="1234567890",
                current_document_json={},
            )
            session.add(document)
            session.commit()
            document_id = document.id
        job_id = _attach_pending_confirmed_result(
            database,
            document_id=document_id,
        )
        drive = _drive_service(database, fake, monkeypatch)

        def update_revision_after_upload(
            method: str,
            url: str,
            **kwargs: object,
        ) -> dict[str, Any]:
            response = fake.request(method, url, **kwargs)
            if "/upload/drive/v3/files" in url and method == "POST":
                with database.session() as session:
                    confirmed = (
                        session.query(ConfirmedResult)
                        .filter_by(job_id=job_id)
                        .one()
                    )
                    confirmed.ocr_result_revision = 2
                    session.commit()
            return response

        monkeypatch.setattr(drive, "_request", update_revision_after_upload)

        with pytest.raises(AppError) as caught:
            drive.create_spreadsheet_from_xlsx(
                document_id=document_id,
                template_id=template_id,
                operation_id=f"{job_id}:1",
                source_job_id=job_id,
                source_revision=1,
                name="古い結果.xlsx",
                content=b"xlsx-data",
            )

        assert caught.value.code == "SPREADSHEET_OUTPUT_STALE"
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.spreadsheet_id is None
            assert document.drive_google_subject is None
        assert [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get("llmOcrSpreadsheetOperationId")
            == f"{job_id}:1"
        ]
    finally:
        database.dispose()


def test_xlsx_output_from_superseded_job_cannot_replace_current_ledger_sheet(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        template_id, _ = _seed_two_roots(database)
        with database.session() as session:
            document = LedgerDocument(
                template_id=template_id,
                individual_id="1234567890",
                current_document_json={},
                spreadsheet_id="current-sheet",
                spreadsheet_url=(
                    "https://docs.google.com/spreadsheets/d/current-sheet/edit"
                ),
                drive_google_subject="subject-1",
                drive_root_folder_id="root-a",
                drive_folder_id="root-a",
            )
            session.add(document)
            session.commit()
            document_id = document.id
        old_job_id = _attach_pending_confirmed_result(
            database,
            document_id=document_id,
        )
        with database.session() as session:
            current_job = ProcessingJob(
                job_type="EXTRACT_DOCUMENT",
                status="CONFIRMED",
                request_hash="f" * 64,
                config_snapshot_json={},
            )
            session.add(current_job)
            session.flush()
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            document.current_job_id = current_job.id
            session.commit()
        drive = _drive_service(database, fake, monkeypatch)

        with pytest.raises(AppError) as caught:
            drive.create_spreadsheet_from_xlsx(
                document_id=document_id,
                template_id=template_id,
                operation_id=f"{old_job_id}:1",
                source_job_id=old_job_id,
                source_revision=1,
                name="旧ジョブ.xlsx",
                content=b"xlsx-data",
            )

        assert caught.value.code == "SPREADSHEET_OUTPUT_STALE"
        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.spreadsheet_id == "current-sheet"
        assert not [
            item
            for item in fake.files.values()
            if item.get("appProperties", {}).get("llmOcrSpreadsheetOperationId")
            == f"{old_job_id}:1"
        ]
    finally:
        database.dispose()


def test_stale_root_mapping_does_not_block_current_folder_rename(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "保存先変更前")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        stale_folder = fake.find_one(name="保存先変更前", parent_id="root-a")
        fake.add_folder(
            "root-c",
            "新しい既定保存先",
            parents=[],
            record_change=False,
        )
        with database.session() as session:
            account = session.get(GoogleOAuthAccount, "subject-1")
            assert account is not None
            account.default_output_folder_id = "root-c"
            account.default_output_folder_name = "新しい既定保存先"
            account.default_output_folder_path = "新しい既定保存先"
            session.commit()
        assert drive.sync_document_library()["status"] == "SYNCED"
        fake.external_remove(str(stale_folder["id"]))

        renamed = DocumentLibraryService(
            database,
            google_drive=drive,
        ).rename_folder(folder_id, name="保存先変更後")

        assert renamed["name"] == "保存先変更後"
        assert fake.find_one(name="保存先変更後", parent_id="root-b")
        assert fake.find_one(name="保存先変更後", parent_id="root-c")
        assert fake.files[str(stale_folder["id"])]["name"] == "保存先変更前"
    finally:
        database.dispose()


def test_retry_converges_after_name_update_succeeds_but_move_fails(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        first_parent_id = _create_local_folder(database, "移動元")
        second_parent_id = _create_local_folder(database, "移動先")
        child_id = _create_local_folder(
            database,
            "変更前",
            parent_id=first_parent_id,
        )
        drive = _drive_service(database, fake, monkeypatch)
        for folder_id in (first_parent_id, second_parent_id, child_id):
            drive.mirror_library_folder(folder_id)
        remote_child_a = fake.find_one(
            name="変更前",
            parent_id=str(
                fake.find_one(name="移動元", parent_id="root-a")["id"]
            ),
        )
        with database.session() as session:
            child = session.get(DocumentFolder, child_id)
            assert child is not None
            child.name = "変更後"
            child.normalized_name = "変更後".casefold()
            child.parent_id = second_parent_id
            session.commit()
        fake.fail_file_move_id = str(remote_child_a["id"])

        first = drive.sync_document_library()
        second = drive.sync_document_library()

        assert first["status"] == "ERROR"
        assert second["status"] == "SYNCED"
        for root_id in ("root-a", "root-b"):
            remote_parent = fake.find_one(name="移動先", parent_id=root_id)
            assert fake.find_one(
                name="変更後",
                parent_id=str(remote_parent["id"]),
            )
    finally:
        database.dispose()


def test_same_folder_conflicts_are_reported_per_drive_root(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "競合前")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        with database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            assert folder is not None
            folder.name = "ローカル変更"
            folder.normalized_name = "ローカル変更".casefold()
            session.commit()
        fake.external_update(
            str(fake.find_one(name="競合前", parent_id="root-a")["id"]),
            name="Drive A変更",
        )
        fake.external_update(
            str(fake.find_one(name="競合前", parent_id="root-b")["id"]),
            name="Drive B変更",
        )

        outcome = drive.sync_document_library()

        assert outcome["status"] == "PARTIAL"
        assert outcome["conflicts"] == 2
        assert {item["driveRootId"] for item in outcome["conflictItems"]} == {
            "root-a",
            "root-b",
        }
        assert {item["driveRootName"] for item in outcome["conflictItems"]} == {
            "共通保存先",
            "テンプレート別保存先",
        }
    finally:
        database.dispose()


def test_deleting_folder_preserves_inaccessible_other_account_mirror(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(settings_factory)
    fake = _FakeDrive()
    try:
        _seed_two_roots(database)
        folder_id = _create_local_folder(database, "削除対象")
        drive = _drive_service(database, fake, monkeypatch)
        drive.mirror_library_folder(folder_id)
        fake.add_folder(
            "foreign-root",
            "別アカウント保存先",
            parents=[],
            record_change=False,
        )
        fake.add_folder(
            "foreign-folder",
            "削除対象",
            parents=["foreign-root"],
            record_change=False,
        )
        with database.session() as session:
            session.add(
                GoogleOAuthAccount(
                    google_subject="subject-2",
                    email="other@example.com",
                    encrypted_refresh_token="encrypted",
                    scopes="https://www.googleapis.com/auth/drive.file",
                    default_output_folder_id="foreign-root",
                )
            )
            session.commit()
            session.add(
                DocumentFolderDriveMapping(
                    google_subject="subject-2",
                    document_folder_id=folder_id,
                    drive_root_folder_id="foreign-root",
                    drive_folder_id="foreign-folder",
                    drive_name="削除対象",
                    drive_parent_folder_id="foreign-root",
                )
            )
            session.commit()

        DocumentLibraryService(database, google_drive=drive).delete_folder(folder_id)

        with database.session() as session:
            assert session.get(DocumentFolder, folder_id) is None
            assert (
                session.query(DocumentFolderDriveMapping)
                .filter_by(document_folder_id=folder_id)
                .count()
                == 0
            )
        assert all(
            item.get("trashed") is True
            for item in fake.files.values()
            if item.get("appProperties", {}).get("llmOcrLibraryFolderId")
            == folder_id
        )
        assert fake.files["foreign-folder"]["trashed"] is False
    finally:
        database.dispose()
