from collections.abc import Callable

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    ConfirmedResult,
    LedgerDocument,
    LedgerDocumentPage,
    OcrResult,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.ocr_factory import blank_ocr_document
from app.services.document_reconciliation_service import DocumentReconciliationService
from app.validation.business_validators import DeterministicValidator


def _document(name: str, individual_id: str = "1234567890") -> dict[str, object]:
    document = blank_ocr_document(provider="mock", source_regions=[]).model_dump(
        mode="json", by_alias=True
    )
    document["document"]["unifiedEarTag"].update(
        raw=individual_id, normalized=individual_id, status="readable"
    )
    document["document"]["cattleName"].update(raw=name, normalized=name, status="readable")
    return document


def _generic_document(name: str) -> dict[str, object]:
    document = blank_ocr_document(provider="mock", source_regions=[]).model_dump(
        mode="json", by_alias=True
    )
    document["document"]["cattleName"].update(raw=name, normalized=name, status="readable")
    return document


def test_spreadsheet_document_can_be_resolved_without_cattle_identity(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    service = DocumentReconciliationService(DeterministicValidator())
    try:
        with database.session() as session:
            template = Template(name="面接カード", document_type="spreadsheet")
            session.add(template)
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode="SPREADSHEET_TEMPLATE",
                status="ACTIVE",
                canonical_width=1000,
                canonical_height=1400,
                config_relative_path="templates/interview.json",
                quality_json={},
                layout_json={},
            )
            session.add(version)
            session.flush()
            job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                template_version_id=version.id,
                request_hash="f" * 64,
                config_snapshot_json={},
            )
            session.add(job)
            session.flush()
            document = _generic_document("応募者")
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
            session.add(result)
            session.flush()

            service.resolve(
                session,
                job=job,
                stored=result,
                action="NEW_DOCUMENT",
                target_document_id=None,
                target_job_id=None,
                target_page_id=None,
                configured_paths={"genericValues.fullName"},
            )
            session.commit()

            ledger = session.get(LedgerDocument, job.id)
            page = session.query(LedgerDocumentPage).filter_by(job_id=job.id).one()
            assert ledger is not None
            assert ledger.individual_id == service.document_key(job.id)
            assert len(ledger.individual_id) == 10
            assert page.relation_type == "NEW_DOCUMENT"
    finally:
        database.dispose()


def test_same_identity_requires_choice_and_add_page_merges_into_one_document(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    service = DocumentReconciliationService(DeterministicValidator())
    try:
        with database.session() as session:
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
            old_job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.CONFIRMED.value,
                template_version_id=version.id,
                request_hash="a" * 64,
                config_snapshot_json={},
            )
            new_job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                template_version_id=version.id,
                request_hash="b" * 64,
                config_snapshot_json={},
            )
            session.add_all([old_job, new_job])
            session.flush()
            old_document = _document("こよみ")
            incoming = _document("こよみ改")
            session.add(
                ConfirmedResult(
                    job_id=old_job.id,
                    individual_id="1234567890",
                    confirmed_json=old_document,
                    ocr_result_revision=1,
                    export_relative_path="exports/old.json",
                )
            )
            result = OcrResult(
                job_id=new_job.id,
                schema_version="1.0",
                provider="mock",
                raw_candidate_json=incoming,
                current_corrected_json=incoming,
                validation_json={},
                revision=1,
                api_call_count=0,
            )
            session.add(result)
            session.commit()

            payload = service.payload(session, job=new_job, document_json=incoming)
            assert payload["required"] is True
            assert payload["candidates"][0]["sourceJobId"] == old_job.id

            updated = service.resolve(
                session,
                job=new_job,
                stored=result,
                action="ADD_PAGE",
                target_document_id=None,
                target_job_id=old_job.id,
                target_page_id=None,
                configured_paths=set(),
            )
            session.commit()

            resolved = service.payload(
                session, job=new_job, document_json=updated.current_corrected_json
            )
            assert resolved["resolved"] is True
            assert resolved["action"] == "ADD_PAGE"
            assert resolved["documentId"] == old_job.id
            assert any(change["path"] == "document.cattleName" for change in resolved["changes"])
            pages = session.query(LedgerDocumentPage).order_by(LedgerDocumentPage.page_number).all()
            assert [page.page_number for page in pages] == [1, 2]

            rescan_job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                template_version_id=version.id,
                request_hash="c" * 64,
                config_snapshot_json={},
            )
            session.add(rescan_job)
            session.flush()
            rescan_document = _document("こよみ再読取")
            rescan_result = OcrResult(
                job_id=rescan_job.id,
                schema_version="1.0",
                provider="mock",
                raw_candidate_json=rescan_document,
                current_corrected_json=rescan_document,
                validation_json={},
                revision=1,
                api_call_count=0,
            )
            session.add(rescan_result)
            session.flush()

            service.resolve(
                session,
                job=rescan_job,
                stored=rescan_result,
                action="RESCAN_PAGE",
                target_document_id=old_job.id,
                target_job_id=None,
                target_page_id=pages[0].id,
                configured_paths=set(),
            )
            session.commit()

            original_page = session.get(LedgerDocumentPage, pages[0].id)
            replacement_page = (
                session.query(LedgerDocumentPage)
                .filter(LedgerDocumentPage.job_id == rescan_job.id)
                .one()
            )
            assert original_page is not None and original_page.active is True
            assert replacement_page is not None and replacement_page.active is False

            ledger = session.get(LedgerDocument, old_job.id)
            assert ledger is not None
            service.mark_confirmed(
                session,
                ledger,
                replacement_page,
                job_id=rescan_job.id,
                document_json=rescan_document,
                individual_id="1234567890",
            )
            session.commit()
            session.refresh(original_page)
            session.refresh(replacement_page)
            assert original_page.active is False
            assert replacement_page.active is True
            assert replacement_page.confirmed_at is not None
    finally:
        database.dispose()


def test_combined_confirmation_requires_review_only_when_every_value_differs(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    service = DocumentReconciliationService(DeterministicValidator())
    try:
        with database.session() as session:
            template = Template(name="複合照合", document_type="breeding-ledger")
            session.add(template)
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode="REFERENCE_TEMPLATE",
                status="ACTIVE",
                canonical_width=1000,
                canonical_height=1400,
                config_relative_path="templates/combined.json",
                quality_json={},
                layout_json={},
            )
            session.add(version)
            session.flush()
            old_job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.CONFIRMED.value,
                template_version_id=version.id,
                request_hash="d" * 64,
                config_snapshot_json={},
            )
            identity_snapshot = {
                "templateSnapshot": {
                    "config": {
                        "documentIdentityFields": [
                            {
                                "targetPath": "document.cattleName",
                                "matchRole": "confirmation",
                            },
                            {
                                "targetPath": "document.unifiedEarTag",
                                "matchRole": "confirmation",
                            },
                        ]
                    }
                }
            }
            new_job = ProcessingJob(
                job_type=JobType.EXTRACT_DOCUMENT.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                template_version_id=version.id,
                request_hash="e" * 64,
                config_snapshot_json=identity_snapshot,
            )
            session.add_all([old_job, new_job])
            session.flush()
            old_document = _document("こよみ", "1234567890")
            session.add(
                LedgerDocument(
                    id=old_job.id,
                    template_id=template.id,
                    individual_id="1234567890",
                    current_job_id=old_job.id,
                    current_document_json=old_document,
                )
            )
            session.flush()

            both_different = service.payload(
                session,
                job=new_job,
                document_json=_document("さくら", "9876543210"),
            )
            assert both_different["required"] is True
            assert both_different["candidates"][0]["documentId"] == old_job.id

            name_matches = service.payload(
                session,
                job=new_job,
                document_json=_document("こよみ", "9876543210"),
            )
            assert name_matches["required"] is False
            assert name_matches["candidates"] == []
    finally:
        database.dispose()
