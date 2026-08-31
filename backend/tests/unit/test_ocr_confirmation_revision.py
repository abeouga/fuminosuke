from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import (
    ConfirmedResult,
    LedgerDocument,
    LedgerDocumentPage,
    OcrResult,
    ProcessingJob,
    SpreadsheetOutputOperation,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.ocr_factory import blank_ocr_document
from app.services.ocr_result_service import OcrResultService
from app.storage.local_storage import LocalStorageService


class _NameReconcilingDrive:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str | None]] = []

    def reconcile_document_name(
        self,
        document_id: str,
        desired_name: str,
        *,
        operation_id: str,
        expected_subject: str | None = None,
    ) -> None:
        self.calls.append(
            (document_id, desired_name, operation_id, expected_subject)
        )


def test_confirm_api_requires_base_revision(client: TestClient) -> None:
    response = client.post("/api/v1/ocr-jobs/missing-job/confirm")

    assert response.status_code == 422


def test_confirm_rejects_a_revision_the_user_did_not_view(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="c" * 64,
                config_snapshot_json={},
            )
            session.add(job)
            session.flush()
            session.add(
                OcrResult(
                    job_id=job.id,
                    schema_version="1.0",
                    model_id=None,
                    prompt_version=None,
                    provider="mock",
                    raw_candidate_json={},
                    current_corrected_json={},
                    validation_json={},
                    revision=2,
                    api_call_count=0,
                    input_tokens=None,
                    output_tokens=None,
                    estimated_cost=None,
                )
            )
            session.commit()
            job_id = job.id

        with pytest.raises(AppError) as caught:
            OcrResultService(database, storage).confirm(
                job_id,
                base_revision=1,
            )

        assert caught.value.code == "REVISION_CONFLICT"
        with database.session() as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            assert job.status == JobStatus.REVIEW_REQUIRED.value
            assert (
                session.query(ConfirmedResult)
                .filter(ConfirmedResult.job_id == job_id)
                .one_or_none()
                is None
            )
    finally:
        database.dispose()


def test_confirm_reconciles_stale_remote_name_even_when_local_name_is_unchanged(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    drive = _NameReconcilingDrive()
    document = blank_ocr_document(provider="mock", source_regions=[]).model_dump(
        mode="json", by_alias=True
    )
    try:
        with database.session() as session:
            template = Template(name="面接カード", document_type="generic-form")
            session.add(template)
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode="REFERENCE_TEMPLATE",
                status="ACTIVE",
                canonical_width=1000,
                canonical_height=1400,
                config_relative_path="templates/interview.json",
                quality_json={},
                layout_json={},
            )
            session.add(version)
            session.flush()
            template.active_version_id = version.id
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                template_version_id=version.id,
                request_hash="n" * 64,
                config_snapshot_json={"fieldMappings": []},
            )
            session.add(job)
            session.flush()
            session.add(
                OcrResult(
                    job_id=job.id,
                    schema_version="1.0",
                    model_id=None,
                    prompt_version=None,
                    provider="mock",
                    raw_candidate_json=document,
                    current_corrected_json=document,
                    validation_json={},
                    revision=1,
                    api_call_count=0,
                    input_tokens=None,
                    output_tokens=None,
                    estimated_cost=None,
                )
            )
            ledger = LedgerDocument(
                id=job.id,
                template_id=template.id,
                individual_id="0000000001",
                current_job_id=job.id,
                current_document_json=document,
                spreadsheet_id="sheet-1",
                drive_google_subject="subject-1",
                drive_root_folder_id="root-1",
                drive_folder_id="folder-1",
                document_name="確定書類名",
                library_name="確定書類名",
            )
            session.add(ledger)
            session.flush()
            session.add_all(
                [
                    LedgerDocumentPage(
                        ledger_document_id=ledger.id,
                        job_id=job.id,
                        page_number=1,
                        relation_type="NEW_DOCUMENT",
                        page_content_json=document,
                    ),
                    SpreadsheetOutputOperation(
                        operation_id=f"document-create:{ledger.id}",
                        document_id=ledger.id,
                        google_subject="subject-1",
                        source_job_id=job.id,
                        source_revision=1,
                        drive_root_folder_id="root-1",
                        drive_parent_folder_id="folder-1",
                        drive_name="OCR時の仮名",
                        library_location_version=1,
                        spreadsheet_id="sheet-1",
                    ),
                ]
            )
            session.commit()
            job_id = job.id

        response = OcrResultService(
            database,
            storage,
            google_drive=drive,  # type: ignore[arg-type]
        ).confirm(
            job_id,
            base_revision=1,
            document_name="確定書類名",
        )

        assert response["confirmed"] is True
        assert drive.calls == [
            (job_id, "確定書類名", f"name:{job_id}:1", None)
        ]
        with database.session() as session:
            intent = session.get(
                SpreadsheetOutputOperation,
                f"name:{job_id}:1",
            )
            assert intent is not None
            assert intent.drive_name == "OCR時の仮名"
            assert intent.spreadsheet_id == "sheet-1"
    finally:
        database.dispose()
