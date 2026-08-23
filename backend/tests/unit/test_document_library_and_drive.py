from collections.abc import Callable

import pytest
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.core.config import Settings
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

        assert payload == {"count": 0, "items": []}
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

        def credentials_provider() -> Credentials:
            raise AssertionError("テストではGoogle APIを呼び出しません。")

        drive = GoogleDriveService(database, credentials_provider, lambda: "subject-1")
        monkeypatch.setattr(
            drive,
            "_create_file",
            lambda **kwargs: {"id": "spreadsheet-1", **kwargs},
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
            lambda: credentials,
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
        }
        with database.session() as session:
            account = session.get(GoogleOAuthAccount, "subject-1")
            assert account is not None
            assert account.default_output_folder_id == "picked-folder"
            assert account.default_output_folder_path == "2026年度"
    finally:
        database.dispose()
