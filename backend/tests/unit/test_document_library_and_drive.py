from collections.abc import Callable

import pytest
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import (
    ConfirmedResult,
    GoogleOAuthAccount,
    LedgerDocument,
    LedgerDocumentPage,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.services.document_library_service import DocumentLibraryService
from app.services.google_drive_service import GoogleDriveService
from app.storage.local_storage import LocalStorageService


def _document(name: str) -> dict[str, object]:
    return {
        "document": {
            "unifiedEarTag": {"raw": "1234567890", "normalized": "1234567890"},
            "cattleName": {"raw": name, "normalized": name},
        },
        "saleCalves": [],
        "calvings": [],
        "pedigree": {},
        "reviewItems": [],
    }


def _template_and_job(session: Session) -> tuple[Template, TemplateVersion, ProcessingJob]:
    template = Template(name="繁殖台帳", document_type="breeding-ledger")
    session.add(template)
    session.flush()
    version = TemplateVersion(
        template_id=template.id,
        version_number=1,
        mode="REFERENCE_TEMPLATE",
        status="ACTIVE",
        canonical_width=1000,
        canonical_height=1400,
        config_relative_path="templates/test.json",
        quality_json={},
        layout_json={},
    )
    session.add(version)
    session.flush()
    job = ProcessingJob(
        job_type=JobType.EXTRACT_DOCUMENT.value,
        status=JobStatus.CONFIRMED.value,
        template_version_id=version.id,
        request_hash="d" * 64,
        config_snapshot_json={},
    )
    session.add(job)
    session.flush()
    return template, version, job


def test_library_backfills_confirmed_jobs_and_groups_pages(
    settings_factory: Callable[..., Settings],
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            _, _, job = _template_and_job(session)
            session.add(
                ConfirmedResult(
                    job_id=job.id,
                    ocr_result_revision=1,
                    individual_id="1234567890",
                    confirmed_json=_document("こよみ"),
                    export_relative_path="exports/test.json",
                )
            )
            session.commit()

        payload = DocumentLibraryService(database).list_library()

        assert payload["count"] == 1
        assert payload["items"][0]["individualId"] == "1234567890"
        assert payload["items"][0]["ledgerCount"] == 1
        assert payload["items"][0]["ledgers"][0]["pages"][0]["sourceJobId"] == job.id
        with database.session() as session:
            assert session.query(LedgerDocument).count() == 1
            assert session.query(LedgerDocumentPage).count() == 1
    finally:
        database.dispose()


@pytest.mark.parametrize("operation", ["list", "sync"])
def test_library_backfills_current_spreadsheet_for_already_linked_page(
    settings_factory: Callable[..., Settings],
    operation: str,
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            template, _, job = _template_and_job(session)
            ledger = LedgerDocument(
                id="document-linked",
                template_id=template.id,
                individual_id="1234567890",
                current_job_id=job.id,
                current_document_json=_document("こよみ"),
            )
            session.add(ledger)
            session.add(
                LedgerDocumentPage(
                    ledger_document_id=ledger.id,
                    job_id=job.id,
                    page_number=1,
                    relation_type="NEW_DOCUMENT",
                    active=True,
                    page_content_json=_document("こよみ"),
                    confirmed_at=job.created_at,
                )
            )
            session.add(
                ConfirmedResult(
                    job_id=job.id,
                    ocr_result_revision=1,
                    individual_id="1234567890",
                    confirmed_json=_document("こよみ"),
                    export_relative_path="exports/linked.json",
                    spreadsheet_id="sheet-linked",
                    spreadsheet_url=(
                        "https://docs.google.com/spreadsheets/d/sheet-linked/edit"
                    ),
                    spreadsheet_name="確定済み書類",
                    spreadsheet_sync_status="synced",
                )
            )
            session.commit()

        if operation == "list":
            DocumentLibraryService(database).list_library()
        else:
            class DriveSyncStub:
                def sync_document_library(
                    self,
                    _expected_subject: str | None = None,
                ) -> dict[str, str]:
                    with database.session() as session:
                        current = session.get(LedgerDocument, "document-linked")
                        assert current is not None
                        assert current.spreadsheet_id == "sheet-linked"
                    return {"status": "SYNCED"}

            result = DocumentLibraryService(
                database,
                google_drive=DriveSyncStub(),  # type: ignore[arg-type]
            ).sync_document_library()
            assert result == {"status": "SYNCED"}

        with database.session() as session:
            current = session.get(LedgerDocument, "document-linked")
            assert current is not None
            assert current.spreadsheet_id == "sheet-linked"
            assert current.spreadsheet_url == (
                "https://docs.google.com/spreadsheets/d/sheet-linked/edit"
            )
            assert current.spreadsheet_sync_status == "synced"
            assert current.library_name == "確定済み書類"
            assert current.document_name == "確定済み書類"
    finally:
        database.dispose()


def test_library_uses_configured_title_and_subtitle(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    storage.write_text(
        "templates/test.json",
        """templateId: identity-test
templateVersion: 1
name: 繁殖台帳
canonicalWidth: 300
canonicalHeight: 100
registration:
  preferredMethod: ORB_HOMOGRAPHY
  minFeatureMatches: 4
  minInliers: 4
  minInlierRatio: 0.3
  maxReprojectionErrorPx: 8
  minEccScore: 0.4
regions: []
fieldMappings:
  - mappingId: individual_id
    targetPath: document.unifiedEarTag
    label: 個体識別番号
    x: 0
    y: 0
    width: 100
    height: 30
  - mappingId: cattle_name
    targetPath: document.cattleName
    label: 牛名
    x: 110
    y: 0
    width: 100
    height: 30
documentIdentity:
  mappingId: individual_id
  targetPath: document.unifiedEarTag
  label: 個体識別番号
  valueType: digits
  exactLength: 10
  matchRole: primary
  displayRole: subtitle
documentIdentityFields:
  - mappingId: individual_id
    targetPath: document.unifiedEarTag
    label: 個体識別番号
    valueType: digits
    exactLength: 10
    matchRole: primary
    displayRole: subtitle
  - mappingId: cattle_name
    targetPath: document.cattleName
    label: 牛名
    valueType: text
    exactLength: null
    matchRole: confirmation
    displayRole: title
marks: []
export:
  csvColumns: []
""",
    )
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            _, _, job = _template_and_job(session)
            session.add(
                ConfirmedResult(
                    job_id=job.id,
                    ocr_result_revision=1,
                    individual_id="1234567890",
                    confirmed_json=_document("こよみ"),
                    export_relative_path="exports/test.json",
                )
            )
            session.commit()

        payload = DocumentLibraryService(database, storage).list_library()

        item = payload["items"][0]
        assert item["displayTitle"] == "こよみ"
        assert item["displayTitleLabel"] == "牛名"
        assert item["displaySubtitle"] == "1234567890"
        assert item["displaySubtitleLabel"] == "個体識別番号"
    finally:
        database.dispose()


def test_library_hides_unconfirmed_new_documents(
    settings_factory: Callable[..., Settings],
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            template, _, job = _template_and_job(session)
            ledger = LedgerDocument(
                id=job.id,
                template_id=template.id,
                individual_id="1234567890",
                current_job_id=job.id,
                current_document_json=_document("確認前"),
            )
            session.add(ledger)
            session.add(
                LedgerDocumentPage(
                    ledger_document_id=ledger.id,
                    job_id=job.id,
                    page_number=1,
                    relation_type="NEW_DOCUMENT",
                    active=True,
                    page_content_json=_document("確認前"),
                    confirmed_at=None,
                )
            )
            session.commit()

        payload = DocumentLibraryService(database).list_library()

        assert payload == {"count": 0, "items": [], "folders": [], "documents": []}
    finally:
        database.dispose()


class _DriveRenameStub:
    def __init__(self, *, fail_restore: bool = False) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.current_name = "変更前"
        self.fail_restore = fail_restore

    def rename_spreadsheet(
        self,
        spreadsheet_id: str,
        name: str,
        expected_subject: str | None = None,
        *,
        baseline_name: str | None = None,
    ) -> str:
        del expected_subject
        self.calls.append((spreadsheet_id, name, baseline_name))
        if self.fail_restore and name == "変更前":
            raise RuntimeError("simulated Drive rename compensation failure")
        self.current_name = name
        return name


def test_library_renames_drive_sheet_and_soft_deletes_document(
    settings_factory: Callable[..., Settings],
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            _, _, job = _template_and_job(session)
            session.add(
                ConfirmedResult(
                    job_id=job.id,
                    ocr_result_revision=1,
                    individual_id="1234567890",
                    confirmed_json=_document("こよみ"),
                    export_relative_path="exports/test.json",
                    spreadsheet_id="sheet-1",
                    spreadsheet_url="https://docs.google.com/spreadsheets/d/sheet-1/edit",
                    spreadsheet_name="変更前",
                )
            )
            session.commit()

        DocumentLibraryService(database).list_library()
        drive = _DriveRenameStub()
        service = DocumentLibraryService(database, google_drive=drive)  # type: ignore[arg-type]

        renamed = service.rename_document(job.id, name="変更後")

        assert renamed == {
            "documentId": job.id,
            "name": "変更後",
            "spreadsheetRenamed": True,
        }
        assert drive.calls == [("sheet-1", "変更後", "変更前")]
        with database.session() as session:
            document = session.get(LedgerDocument, job.id)
            confirmed = session.query(ConfirmedResult).filter_by(job_id=job.id).one()
            assert document is not None
            assert document.library_name == "変更後"
            assert confirmed.spreadsheet_name == "変更後"

        deleted = service.delete_documents([job.id])

        assert deleted == {"deletedCount": 1, "driveFilesPreserved": True}
        assert service.list_library()["documents"] == []
        with database.session() as session:
            document = session.get(LedgerDocument, job.id)
            assert document is not None
            assert document.library_deleted_at is not None
            assert (
                session.query(LedgerDocumentPage)
                .filter_by(ledger_document_id=job.id)
                .count()
                == 1
            )
    finally:
        database.dispose()


@pytest.mark.parametrize(
    ("fail_restore", "expected_code", "expected_remote_name"),
    [
        (False, "DRIVE_DOCUMENT_RENAME_ROLLED_BACK", "変更前"),
        (True, "DRIVE_DOCUMENT_RENAME_PARTIAL", "変更後"),
    ],
)
def test_library_document_rename_compensates_database_commit_failure(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    fail_restore: bool,
    expected_code: str,
    expected_remote_name: str,
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            _, _, job = _template_and_job(session)
            session.add(
                ConfirmedResult(
                    job_id=job.id,
                    ocr_result_revision=1,
                    individual_id="1234567890",
                    confirmed_json=_document("こよみ"),
                    export_relative_path="exports/test.json",
                    spreadsheet_id="sheet-rename-commit-failure",
                    spreadsheet_url=(
                        "https://docs.google.com/spreadsheets/d/"
                        "sheet-rename-commit-failure/edit"
                    ),
                    spreadsheet_name="変更前",
                )
            )
            session.commit()
            job_id = job.id

        DocumentLibraryService(database).list_library()
        drive = _DriveRenameStub(fail_restore=fail_restore)
        service = DocumentLibraryService(
            database,
            google_drive=drive,  # type: ignore[arg-type]
        )
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
            service.rename_document(job_id, name="変更後")

        assert caught.value.code == expected_code
        assert drive.current_name == expected_remote_name
        with database.session() as session:
            document = session.get(LedgerDocument, job_id)
            confirmed = session.query(ConfirmedResult).filter_by(job_id=job_id).one()
            assert document is not None
            assert document.library_name == "変更前"
            assert document.document_name == "変更前"
            assert confirmed.spreadsheet_name == "変更前"
    finally:
        database.dispose()


def test_drive_creates_one_spreadsheet_and_persists_document_binding(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            template, _, job = _template_and_job(session)
            session.add(
                GoogleOAuthAccount(
                    google_subject="subject-1",
                    email="user@example.com",
                    encrypted_refresh_token="encrypted",
                    scopes="https://www.googleapis.com/auth/drive.file",
                    drive_root_folder_id="root-folder",
                    drive_root_folder_name="帳票OCR",
                    default_output_folder_id="year-folder",
                    default_output_folder_name="2026年度",
                    default_output_folder_path="帳票OCR > 2026年度",
                )
            )
            session.add(
                LedgerDocument(
                    id=job.id,
                    template_id=template.id,
                    individual_id="1234567890",
                    current_job_id=job.id,
                    current_document_json=_document("こよみ"),
                )
            )
            session.commit()

        def credentials_provider(_subject: str | None = None) -> Credentials:
            raise AssertionError("テストではGoogle APIを呼び出しません。")

        drive = GoogleDriveService(database, credentials_provider, lambda: "subject-1")
        monkeypatch.setattr(
            drive,
            "_create_file",
            lambda **kwargs: {"id": "spreadsheet-1", **kwargs},
        )
        monkeypatch.setattr(drive, "_find_spreadsheet_outputs", lambda *_args: [])
        monkeypatch.setattr(
            drive,
            "_drive_item",
            lambda file_id: {
                "id": file_id,
                "name": "1234567890_こよみ_繁殖台帳",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": ["year-folder"],
                "trashed": False,
                "appProperties": {
                    "llmOcrLedgerDocumentId": job.id,
                    "llmOcrDriveRootId": "year-folder",
                    "llmOcrSpreadsheetOperationId": f"document-create:{job.id}",
                },
            },
        )

        first = drive.create_document_spreadsheet(job.id, "OCR")
        second = drive.create_document_spreadsheet(job.id, "OCR")

        assert first == second
        assert first[0] == "spreadsheet-1"
        with database.session() as session:
            ledger = session.get(LedgerDocument, job.id)
            assert ledger is not None
            assert ledger.drive_folder_id == "year-folder"
            assert ledger.drive_folder_path == "帳票OCR > 2026年度"
            assert ledger.spreadsheet_id == "spreadsheet-1"
    finally:
        database.dispose()


def test_drive_picker_session_and_selected_folder_are_persisted(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            session.add(
                GoogleOAuthAccount(
                    google_subject="subject-1",
                    email="user@example.com",
                    encrypted_refresh_token="encrypted",
                    scopes="https://www.googleapis.com/auth/drive.file",
                )
            )
            session.commit()

        credentials = Credentials(token="short-lived-token")
        monkeypatch.setattr(credentials, "refresh", lambda request: None)
        drive = GoogleDriveService(
            database,
            lambda _subject=None: credentials,
            lambda: "subject-1",
            "browser-api-key",
            "123456789",
        )
        monkeypatch.setattr(
            drive,
            "_request",
            lambda *args, **kwargs: {
                "id": "picked-folder",
                "name": "2026年度",
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": False,
                "capabilities": {"canAddChildren": True},
            },
        )

        assert drive.picker_session() == {
            "accessToken": "short-lived-token",
            "developerKey": "browser-api-key",
            "appId": "123456789",
        }
        selected = drive.select_output_folder("picked-folder")

        assert selected == {
            "pickerConfigured": True,
            "pickerConfigurationError": None,
            "folderId": "picked-folder",
            "folderName": "2026年度",
            "roots": [
                {"folderId": "picked-folder", "folderName": "2026年度"}
            ],
        }
        with database.session() as session:
            account = session.get(GoogleOAuthAccount, "subject-1")
            assert account is not None
            assert account.default_output_folder_id == "picked-folder"
            assert account.default_output_folder_path == "2026年度"
    finally:
        database.dispose()


def test_drive_spreadsheet_name_is_updated_in_place(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        def credentials_provider(_subject: str | None = None) -> Credentials:
            raise AssertionError("テストではGoogle APIを呼び出しません。")

        drive = GoogleDriveService(database, credentials_provider, lambda: "subject-1")
        requests: list[tuple[tuple[object, ...], dict[str, object]]] = []
        current_name = "面接カード_変更前"

        def request(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal current_name
            requests.append((args, kwargs))
            if args[0] == "PATCH":
                current_name = "面接カード_阿部桜雅"
            return {
                "id": "spreadsheet-1",
                "name": current_name,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [],
                "trashed": False,
            }

        monkeypatch.setattr(drive, "_request", request)

        renamed = drive.rename_spreadsheet("spreadsheet-1", "面接カード_阿部桜雅")

        assert renamed == "面接カード_阿部桜雅"
        assert [args[0] for args, _kwargs in requests] == [
            "GET",
            "GET",
            "PATCH",
            "GET",
        ]
        assert requests[2][1]["json"] == {"name": "面接カード_阿部桜雅"}
    finally:
        database.dispose()


def test_drive_spreadsheet_file_and_single_result_sheet_are_renamed_idempotently(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    try:
        def credentials_provider(_subject: str | None = None) -> Credentials:
            raise AssertionError("テストではGoogle APIを呼び出しません。")

        drive = GoogleDriveService(database, credentials_provider, lambda: "subject-1")
        calls: list[tuple[str, str]] = []
        current_name = "変更前"
        current_sheet_title = "変更前"

        def request(method: str, url: str, **kwargs: object) -> dict[str, object]:
            nonlocal current_name, current_sheet_title
            calls.append((method, url))
            if "sheets.googleapis.com" in url:
                if method == "POST":
                    body = kwargs["json"]
                    assert isinstance(body, dict)
                    requests = body["requests"]
                    assert isinstance(requests, list)
                    properties = requests[0]["updateSheetProperties"]["properties"]
                    current_sheet_title = properties["title"]
                    return {"replies": [{}]}
                return {
                    "sheets": [
                        {
                            "properties": {
                                "sheetId": 7,
                                "title": current_sheet_title,
                                "index": 0,
                            }
                        }
                    ]
                }
            if method == "PATCH":
                body = kwargs["json"]
                assert isinstance(body, dict)
                current_name = str(body["name"])
            return {
                "id": "spreadsheet-1",
                "name": current_name,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [],
                "modifiedTime": "2026-09-03T00:00:00Z",
                "trashed": False,
            }

        monkeypatch.setattr(drive, "_request", request)

        first = drive.rename_spreadsheet(
            "spreadsheet-1",
            "面接テンプレート_Phase6",
            rename_result_sheet=True,
        )
        calls_after_first = len(calls)
        second = drive.rename_spreadsheet(
            "spreadsheet-1",
            "面接テンプレート_Phase6",
            rename_result_sheet=True,
        )

        assert first == second == "面接テンプレート_Phase6"
        assert current_name == current_sheet_title == "面接テンプレート_Phase6"
        assert len(calls) == calls_after_first + 2
        assert sum(method == "POST" for method, _url in calls) == 1
    finally:
        database.dispose()
