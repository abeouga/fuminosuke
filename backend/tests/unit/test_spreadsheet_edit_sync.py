from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    ConfirmedResult,
    LedgerDocument,
    LedgerDocumentPage,
    OcrResult,
    OcrResultRevision,
    ProcessingJob,
    SpreadsheetEditEvent,
    Template,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, RevisionSource
from app.domain.ocr_models import OcrDocument
from app.services.google_sheets_sync_service import SpreadsheetEdit
from app.services.ocr_result_service import OcrResultService
from app.storage.local_storage import LocalStorageService


def _mock_document() -> OcrDocument:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    return OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))


def _edit(
    *,
    edit_id: str,
    job_id: str,
    field_id: str,
    value: str,
    base_revision: int = 1,
    previous_value: str | None = None,
) -> SpreadsheetEdit:
    return SpreadsheetEdit(
        queue_row=2,
        edit_id=edit_id,
        job_id=job_id,
        record_id="document",
        field_id=field_id,
        previous_value=previous_value,
        new_value=value,
        base_revision=base_revision,
        source_sheet="帳票一覧",
        source_cell="D2",
    )


def test_sheet_edits_are_idempotent_and_safely_rebase_distinct_fields(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    document = _mock_document().model_dump(mode="json", by_alias=True)
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="s" * 64,
                config_snapshot_json={},
            )
            session.add(job)
            session.flush()
            result = OcrResult(
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
            session.add(result)
            session.flush()
            session.add(
                OcrResultRevision(
                    ocr_result_id=result.id,
                    revision=1,
                    source=RevisionSource.OCR.value,
                    result_json=document,
                    validation_json={},
                )
            )
            session.commit()
            job_id = job.id

        service = OcrResultService(database, storage)
        first = service.apply_spreadsheet_edit(
            _edit(
                edit_id="edit-name",
                job_id=job_id,
                field_id="document.cattleName",
                value="修正花子",
                previous_value="モック花子",
            )
        )
        duplicate = service.apply_spreadsheet_edit(
            _edit(
                edit_id="edit-name",
                job_id=job_id,
                field_id="document.cattleName",
                value="修正花子",
                previous_value="モック花子",
            )
        )
        conflicting_duplicate = service.apply_spreadsheet_edit(
            _edit(
                edit_id="edit-name",
                job_id=job_id,
                field_id="document.cattleName",
                value="別の編集",
                previous_value="モック花子",
            )
        )
        rebased = service.apply_spreadsheet_edit(
            _edit(
                edit_id="edit-owner",
                job_id=job_id,
                field_id="document.owner",
                value="所有者A",
            )
        )

        assert first.status == "APPLIED"
        assert first.revision == 2
        assert duplicate.status == "APPLIED"
        assert duplicate.revision == 2
        assert conflicting_duplicate.status == "FAILED"
        assert conflicting_duplicate.error_code == "SHEET_EDIT_ID_CONFLICT"
        assert rebased.status == "APPLIED"
        assert rebased.revision == 3
        with database.session() as session:
            stored = session.query(OcrResult).filter_by(job_id=job_id).one()
            assert stored.current_corrected_json["document"]["cattleName"]["raw"] == "修正花子"
            assert stored.current_corrected_json["document"]["owner"]["raw"] == "所有者A"
            assert session.query(SpreadsheetEditEvent).count() == 2
            assert (
                session.query(OcrResultRevision)
                .filter_by(source=RevisionSource.SHEET_EDIT.value)
                .count()
                == 2
            )
    finally:
        database.dispose()


def test_sheet_edit_rejects_job_from_another_spreadsheet_and_account(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    document = _mock_document().model_dump(mode="json", by_alias=True)
    try:
        with database.session() as session:
            template = Template(name="境界テスト", document_type="test")
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="x" * 64,
                config_snapshot_json={},
            )
            session.add_all((template, job))
            session.flush()
            result = OcrResult(
                job_id=job.id,
                schema_version="1.0",
                provider="mock",
                raw_candidate_json=document,
                current_corrected_json=document,
                validation_json={},
                revision=1,
                api_call_count=0,
            )
            ledger = LedgerDocument(
                template_id=template.id,
                individual_id="1234567890",
                current_job_id=job.id,
                current_document_json=document,
                spreadsheet_id="sheet-b",
                drive_google_subject="subject-b",
            )
            session.add_all((result, ledger))
            session.flush()
            session.add(
                LedgerDocumentPage(
                    ledger_document_id=ledger.id,
                    job_id=job.id,
                    page_number=1,
                    relation_type="ORIGINAL",
                    page_content_json=document,
                )
            )
            session.commit()
            job_id = job.id

        service = OcrResultService(database, storage)
        outcome = service.apply_spreadsheet_edit(
            SpreadsheetEdit(
                queue_row=2,
                edit_id="cross-account-edit",
                job_id=job_id,
                record_id="document",
                field_id="document.cattleName",
                previous_value="モック花子",
                new_value="不正な変更",
                base_revision=1,
                source_sheet="帳票一覧",
                source_cell="D2",
                spreadsheet_id="sheet-a",
                google_subject="subject-a",
            )
        )

        assert outcome.status == "FAILED"
        assert outcome.error_code == "SHEET_EDIT_TARGET_MISMATCH"
        with database.session() as session:
            stored = session.query(OcrResult).filter_by(job_id=job_id).one()
            assert stored.revision == 1
    finally:
        database.dispose()


def test_sheet_edit_rejects_a_stale_change_to_the_same_field(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    document = _mock_document().model_dump(mode="json", by_alias=True)
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="t" * 64,
                config_snapshot_json={},
            )
            session.add(job)
            session.flush()
            result = OcrResult(
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
            session.add(result)
            session.flush()
            session.add(
                OcrResultRevision(
                    ocr_result_id=result.id,
                    revision=1,
                    source=RevisionSource.OCR.value,
                    result_json=document,
                    validation_json={},
                )
            )
            session.commit()
            job_id = job.id

        service = OcrResultService(database, storage)
        service.apply_spreadsheet_edit(
            _edit(
                edit_id="first",
                job_id=job_id,
                field_id="document.cattleName",
                value="先行修正",
                previous_value="モック花子",
            )
        )
        conflict = service.apply_spreadsheet_edit(
            _edit(
                edit_id="stale",
                job_id=job_id,
                field_id="document.cattleName",
                value="後続修正",
            )
        )

        assert conflict.status == "FAILED"
        assert conflict.error_code == "REVISION_CONFLICT"
        with database.session() as session:
            stored = session.query(OcrResult).filter_by(job_id=job_id).one()
            assert stored.current_corrected_json["document"]["cattleName"]["raw"] == "先行修正"
    finally:
        database.dispose()


def test_sheet_edit_refreshes_confirmed_exports_and_snapshot(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    document = _mock_document().model_dump(mode="json", by_alias=True)
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="u" * 64,
                config_snapshot_json={},
            )
            session.add(job)
            session.flush()
            result = OcrResult(
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
            session.add(result)
            session.flush()
            session.add(
                OcrResultRevision(
                    ocr_result_id=result.id,
                    revision=1,
                    source=RevisionSource.OCR.value,
                    result_json=document,
                    validation_json={},
                )
            )
            session.commit()
            job_id = job.id

        service = OcrResultService(database, storage)
        confirmed = service.confirm(job_id, base_revision=1)
        outcome = service.apply_spreadsheet_edit(
            _edit(
                edit_id="confirmed-name",
                job_id=job_id,
                field_id="document.cattleName",
                value="確定後花子",
                previous_value="モック花子",
                base_revision=confirmed["revision"],
            )
        )

        assert outcome.status == "APPLIED"
        with database.session() as session:
            stored_job = session.get(ProcessingJob, job_id)
            snapshot = session.query(ConfirmedResult).filter_by(job_id=job_id).one()
            assert stored_job is not None
            assert stored_job.status == JobStatus.CONFIRMED.value
            assert snapshot.ocr_result_revision == outcome.revision
            assert snapshot.confirmed_json["document"]["cattleName"]["raw"] == "確定後花子"
        exported = storage.resolve(f"exports/{job_id}/confirmed_result.json").read_text(
            encoding="utf-8"
        )
        assert '"raw": "確定後花子"' in exported
    finally:
        database.dispose()
