from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import (
    ConfirmedResult,
    JobArtifact,
    LedgerDocument,
    LedgerDocumentPage,
    OcrResult,
    OcrResultRevision,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import (
    JobStatus,
    JobType,
    RevisionSource,
    TemplateMode,
    TemplateVersionStatus,
)
from app.domain.ocr_factory import blank_ocr_document
from app.domain.ocr_models import (
    BoundingBox,
    FieldReading,
    OcrDocument,
    ReadingStatus,
    ReviewItem,
    SaleCalf,
)
from app.image_processing.artifacts import ArtifactWriter
from app.jobs.handlers import PipelineJobHandler
from app.jobs.runner import JobRunner
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.document_ocr_execution_service import DocumentOcrExecutionService
from app.services.document_reconciliation_service import DocumentReconciliationService
from app.services.extraction_service import ImageExtractionService
from app.services.google_sheets_sync_service import SpreadsheetSyncStatus
from app.services.job_service import JobService, UploadPayload
from app.services.ocr_result_service import CorrectionValues, OcrResultService
from app.services.ocr_workflow_service import OcrWorkflowService
from app.services.template_service import TemplatePreparationService
from app.storage.local_storage import LocalStorageService
from tests.integration.test_ocr_review_api import _active_template, _wait_for
from tests.synthetic.image_factory import encode_png, ledger_image


def _reading(
    value: str,
    source_region_id: str,
    *,
    box: BoundingBox | None = None,
) -> FieldReading:
    return FieldReading(
        raw=value,
        normalized=value,
        status=ReadingStatus.READABLE,
        alternatives=[],
        review_reason=None,
        source_region_id=source_region_id,
        bounding_box=box,
    )


def _document(*, individual_id: str | None = None, cattle_name: str | None = None) -> OcrDocument:
    document = blank_ocr_document(provider="mock", source_regions=[])
    if individual_id is not None:
        document.document.unified_ear_tag = _reading(
            individual_id,
            "manual_mother_individual_id",
        )
    if cattle_name is not None:
        document.document.cattle_name = _reading(cattle_name, "cattle_name_cell")
    return document


def _blank(source_region_id: str) -> FieldReading:
    return FieldReading(
        raw=None,
        normalized=None,
        status=ReadingStatus.BLANK,
        alternatives=[],
        review_reason=None,
        source_region_id=source_region_id,
        bounding_box=None,
    )


def _sale_calf(
    *,
    row: int,
    price: str,
    sire_name: str,
    sire_uncertain: bool = False,
) -> SaleCalf:
    return SaleCalf(
        parity=_blank(f"parity-{row}"),
        age_days=_blank(f"age-{row}"),
        weight_kg=_blank(f"weight-{row}"),
        price_yen=_reading(
            price,
            f"price-{row}",
            box=BoundingBox(x=10, y=row * 60, width=80, height=30),
        ),
        sire_name=FieldReading(
            raw=sire_name,
            normalized=None if sire_uncertain else sire_name,
            status=(ReadingStatus.UNCERTAIN if sire_uncertain else ReadingStatus.READABLE),
            alternatives=["二列目"] if sire_uncertain else [],
            review_reason="OCR_LOW_CONFIDENCE" if sire_uncertain else None,
            source_region_id=f"sire-{row}",
            bounding_box=BoundingBox(x=10, y=row * 60 + 30, width=80, height=30),
        ),
        sale_date=_blank(f"date-{row}"),
        notes=_blank(f"notes-{row}"),
    )


def _seed_review_result(
    database: Database,
    *,
    document_type: str = "generic-form",
    document: OcrDocument | None = None,
) -> tuple[str, str]:
    serialized = (document or _document()).model_dump(mode="json", by_alias=True)
    with database.session() as session:
        template = Template(name="Phase 2 direct service", document_type=document_type)
        session.add(template)
        session.flush()
        version = TemplateVersion(
            template_id=template.id,
            version_number=1,
            mode=TemplateMode.REFERENCE_TEMPLATE.value,
            status=TemplateVersionStatus.ACTIVE.value,
            canonical_width=1000,
            canonical_height=1400,
            config_relative_path="templates/phase2-direct/template.json",
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
            request_hash="d" * 64,
            config_snapshot_json={"fieldMappings": []},
        )
        session.add(job)
        session.flush()
        result = OcrResult(
            job_id=job.id,
            schema_version="1.0",
            model_id="mock-v1",
            prompt_version="phase2",
            provider="mock",
            raw_candidate_json=serialized,
            current_corrected_json=serialized,
            validation_json={"valid": True, "issues": []},
            revision=1,
            api_call_count=0,
        )
        session.add(result)
        session.flush()
        session.add(
            OcrResultRevision(
                ocr_result_id=result.id,
                revision=1,
                source=RevisionSource.OCR.value,
                result_json=serialized,
                validation_json=result.validation_json,
            )
        )
        session.commit()
        return job.id, result.id


def _correction_values(document: OcrDocument, base_revision: int) -> CorrectionValues:
    return CorrectionValues(
        base_revision=base_revision,
        document=document.document,
        sale_calves=document.sale_calves,
        pedigree=document.pedigree,
        calvings=document.calvings,
        custom_fields=document.custom_fields,
        review_items=document.review_items,
    )


def _create_ocr_job(
    client: TestClient,
    version_id: str,
    *,
    seed: int,
    document_action: str | None = None,
    target_document_id: str | None = None,
    target_page_id: str | None = None,
) -> str:
    data: dict[str, str] = {
        "templateVersionId": version_id,
        "motherIndividualId": "1234567890",
        "ocrEnabled": "true",
    }
    if document_action is not None:
        data["documentAction"] = document_action
    if target_document_id is not None:
        data["targetDocumentId"] = target_document_id
    if target_page_id is not None:
        data["targetPageId"] = target_page_id
    response = client.post(
        "/api/v1/ocr-jobs",
        data=data,
        files=[
            (
                "file",
                (
                    "filled.png",
                    encode_png(ledger_image(handwriting_seed=seed)),
                    "image/png",
                ),
            )
        ],
    )
    assert response.status_code == 202, response.text
    return str(response.json()["jobId"])


def test_retry_reprocess_keeps_the_accepted_input_and_template_snapshots(
    client: TestClient,
) -> None:
    version_id = _active_template(client)
    job_id = _create_ocr_job(client, version_id, seed=31)
    _wait_for(client, job_id, JobStatus.REVIEW_REQUIRED.value)

    database = client.app.state.database
    storage = client.app.state.storage
    with database.session() as session:
        job = session.get(ProcessingJob, job_id)
        version = session.get(TemplateVersion, version_id)
        result = session.query(OcrResult).filter_by(job_id=job_id).one()
        assert job is not None
        assert version is not None
        inputs = list(job.inputs)
        assert len(inputs) == 1
        original_input = (
            inputs[0].sha256,
            inputs[0].relative_path,
            inputs[0].content_bytes,
        )
        original_config_snapshot = copy.deepcopy(job.config_snapshot_json)
        original_template_bytes = job.template_snapshot_bytes
        assert original_config_snapshot["fieldMappingHash"]
        assert result.revision == 1

        storage.write_text(version.config_relative_path, "live: template changed after acceptance")
        version.layout_json = {"liveTemplateWasMutated": True}
        version.quality_json = {"layoutVerified": False}
        job.status = JobStatus.FAILED.value
        job.error_code = "PHASE2_FORCED_RETRY"
        job.error_message_safe = "test-only retry setup"
        session.commit()

    retried = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == job_id
    assert retried.json()["attemptNumber"] == 2
    _wait_for(client, job_id, JobStatus.REVIEW_REQUIRED.value)

    with database.session() as session:
        job = session.get(ProcessingJob, job_id)
        result = session.query(OcrResult).filter_by(job_id=job_id).one()
        revisions = (
            session.query(OcrResultRevision)
            .filter_by(ocr_result_id=result.id)
            .order_by(OcrResultRevision.revision)
            .all()
        )
        result_artifacts = (
            session.query(JobArtifact)
            .filter_by(job_id=job_id, artifact_name="result")
            .order_by(JobArtifact.attempt_number)
            .all()
        )
        assert job is not None
        inputs = list(job.inputs)
        assert len(inputs) == 1
        assert (inputs[0].sha256, inputs[0].relative_path, inputs[0].content_bytes) == (
            original_input
        )
        assert job.config_snapshot_json == original_config_snapshot
        assert job.template_snapshot_bytes == original_template_bytes
        assert job.config_snapshot_json["fieldMappingHash"] == (
            original_config_snapshot["fieldMappingHash"]
        )
        assert job.attempt_number == 2
        assert result.revision == 2
        assert [revision.source for revision in revisions] == [
            RevisionSource.OCR.value,
            RevisionSource.REPROCESS.value,
        ]
        assert [artifact.attempt_number for artifact in result_artifacts] == [1, 2]


def test_direct_correction_uses_optimistic_revision_and_records_user_edit(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    try:
        initial = _document()
        initial.sale_calves = [
            _sale_calf(row=0, price="100", sire_name="一行目"),
            _sale_calf(
                row=1,
                price="200",
                sire_name="二行目",
                sire_uncertain=True,
            ),
        ]
        initial.review_items = [
            ReviewItem(
                code="CHECK_SIRE_NAME",
                path="saleCalves.1.sireName",
                message="確認",
                severity="warning",
                source_region_id="sire-1",
            )
        ]
        job_id, result_id = _seed_review_result(database, document=initial)
        service = OcrResultService(database, storage)
        corrected = initial.model_copy(deep=True)
        corrected.document.cattle_name = _reading(
            "正常な画面操作による修正",
            "cattle_name_cell",
            box=BoundingBox(x=120, y=20, width=240, height=80),
        )
        corrected.sale_calves[0].price_yen = _reading(
            "200",
            "price-0",
            box=BoundingBox(x=10, y=0, width=80, height=30),
        )
        corrected.sale_calves[0].sire_name = FieldReading(
            raw="二行目",
            normalized=None,
            status=ReadingStatus.UNCERTAIN,
            alternatives=["二列目"],
            review_reason="OCR_LOW_CONFIDENCE",
            source_region_id="sire-0",
            bounding_box=BoundingBox(x=10, y=30, width=80, height=30),
        )
        corrected.sale_calves[1].price_yen = _reading(
            "100",
            "price-1",
            box=BoundingBox(x=10, y=60, width=80, height=30),
        )
        corrected.sale_calves[1].sire_name = _reading(
            "一行目",
            "sire-1",
            box=BoundingBox(x=10, y=90, width=80, height=30),
        )
        corrected.review_items = [
            ReviewItem(
                code="CHECK_SIRE_NAME",
                path="saleCalves.0.sireName",
                message="確認",
                severity="warning",
                source_region_id="sire-0",
            )
        ]

        with pytest.raises(AppError) as stale:
            service.correct(job_id, _correction_values(corrected, base_revision=2))
        assert stale.value.code == "REVISION_CONFLICT"
        with database.session() as session:
            unchanged = session.get(OcrResult, result_id)
            assert unchanged is not None and unchanged.revision == 1
            assert session.query(OcrResultRevision).filter_by(ocr_result_id=result_id).count() == 1

        response = service.correct(job_id, _correction_values(corrected, base_revision=1))
        assert response["revision"] == 2
        assert response["result"]["document"]["cattleName"] == {
            "raw": "正常な画面操作による修正",
            "normalized": "正常な画面操作による修正",
            "status": "readable",
            "alternatives": [],
            "reviewReason": None,
            "sourceRegionId": "cattle_name_cell",
            "boundingBox": {"x": 120, "y": 20, "width": 240, "height": 80},
        }
        assert response["result"]["saleCalves"][0]["priceYen"] == {
            "raw": "200",
            "normalized": "200",
            "status": "readable",
            "alternatives": [],
            "reviewReason": None,
            "sourceRegionId": "price-0",
            "boundingBox": {"x": 10, "y": 0, "width": 80, "height": 30},
        }
        assert response["result"]["saleCalves"][0]["sireName"] == {
            "raw": "二行目",
            "normalized": None,
            "status": "uncertain",
            "alternatives": ["二列目"],
            "reviewReason": "OCR_LOW_CONFIDENCE",
            "sourceRegionId": "sire-0",
            "boundingBox": {"x": 10, "y": 30, "width": 80, "height": 30},
        }
        assert response["result"]["reviewItems"][0] == {
            "code": "CHECK_SIRE_NAME",
            "path": "saleCalves.0.sireName",
            "message": "確認",
            "severity": "warning",
            "sourceRegionId": "sire-0",
        }
        reloaded = service.get(job_id)
        assert reloaded["result"] == response["result"]

        with database.session() as session:
            stored = session.get(OcrResult, result_id)
            revisions = (
                session.query(OcrResultRevision)
                .filter_by(ocr_result_id=result_id)
                .order_by(OcrResultRevision.revision)
                .all()
            )
            assert stored is not None and stored.revision == 2
            assert [revision.source for revision in revisions] == [
                RevisionSource.OCR.value,
                RevisionSource.USER_EDIT.value,
            ]
            assert revisions[-1].result_json == stored.current_corrected_json
            assert revisions[-1].validation_json == stored.validation_json
    finally:
        database.dispose()


def test_rescan_pipeline_reconciles_before_artifact_and_finishes_before_confirmation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = _active_template(client)
    database = client.app.state.database
    old_document = _document(individual_id="1234567890", cattle_name="再読取前")
    old_json = old_document.model_dump(mode="json", by_alias=True)
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        assert version is not None
        old_job = ProcessingJob(
            job_type=JobType.EXTRACT_DOCUMENT.value,
            status=JobStatus.CONFIRMED.value,
            template_version_id=version_id,
            request_hash="r" * 64,
            config_snapshot_json={"fieldMappings": []},
            confirmed_at=datetime.now(UTC),
        )
        session.add(old_job)
        session.flush()
        ledger = LedgerDocument(
            id=old_job.id,
            template_id=version.template_id,
            individual_id="1234567890",
            current_job_id=old_job.id,
            current_document_json=old_json,
            document_name="再読取前",
            library_name="再読取前",
        )
        session.add(ledger)
        session.flush()
        old_page = LedgerDocumentPage(
            ledger_document_id=ledger.id,
            job_id=old_job.id,
            page_number=1,
            relation_type="NEW_DOCUMENT",
            active=True,
            page_content_json=old_json,
            confirmed_at=datetime.now(UTC),
        )
        session.add(old_page)
        session.commit()
        target_document_id = ledger.id
        target_page_id = old_page.id

    events: list[tuple[str, str]] = []
    original_save = OcrResultRepository.save_provider_result
    original_resolve = DocumentReconciliationService.resolve
    original_artifact_json = ArtifactWriter.json
    original_finish = JobRunner._finish

    def record_save(self: OcrResultRepository, *args: Any, **kwargs: Any) -> OcrResult:
        result = original_save(self, *args, **kwargs)
        events.append((str(kwargs["job_id"]), "provider result persistence"))
        return result

    def record_resolve(
        self: DocumentReconciliationService,
        *args: Any,
        **kwargs: Any,
    ) -> OcrResult:
        result = original_resolve(self, *args, **kwargs)
        events.append((str(kwargs["job"].id), "reconciliation"))
        return result

    def record_artifact(self: ArtifactWriter, *args: Any, **kwargs: Any) -> JobArtifact:
        artifact = original_artifact_json(self, *args, **kwargs)
        if kwargs.get("name") == "result":
            events.append((str(kwargs["job_id"]), "final result artifact"))
        return artifact

    def record_finish(self: JobRunner, job_id: str) -> None:
        original_finish(self, job_id)
        events.append((job_id, "job finish"))

    monkeypatch.setattr(OcrResultRepository, "save_provider_result", record_save)
    monkeypatch.setattr(DocumentReconciliationService, "resolve", record_resolve)
    monkeypatch.setattr(ArtifactWriter, "json", record_artifact)
    monkeypatch.setattr(JobRunner, "_finish", record_finish)

    job_id = _create_ocr_job(
        client,
        version_id,
        seed=32,
        document_action="RESCAN_PAGE",
        target_document_id=target_document_id,
        target_page_id=target_page_id,
    )
    _wait_for(client, job_id, JobStatus.REVIEW_REQUIRED.value)
    assert [event for current_job_id, event in events if current_job_id == job_id] == [
        "provider result persistence",
        "reconciliation",
        "final result artifact",
        "job finish",
    ]

    result_response = client.get(f"/api/v1/ocr-jobs/{job_id}/result")
    assert result_response.status_code == 200
    result_body = result_response.json()
    assert result_body["revision"] == 2
    assert result_body["documentResolution"]["action"] == "RESCAN_PAGE"
    assert result_body["documentResolution"]["documentId"] == target_document_id
    with database.session() as session:
        replacement = session.query(LedgerDocumentPage).filter_by(job_id=job_id).one()
        assert replacement.ledger_document_id == target_document_id
        assert replacement.page_number == 1
        assert replacement.replaces_page_id == target_page_id
        assert replacement.active is False

    corrected_document = result_body["result"]
    corrected_document["document"]["cattleName"].update(
        raw="再読取後",
        normalized="再読取後",
        status="readable",
        alternatives=[],
        reviewReason=None,
    )
    correction = client.put(
        f"/api/v1/ocr-jobs/{job_id}/corrections",
        json={
            "baseRevision": 2,
            "document": corrected_document["document"],
            "saleCalves": corrected_document["saleCalves"],
            "pedigree": corrected_document["pedigree"],
            "calvings": corrected_document["calvings"],
            "customFields": corrected_document["customFields"],
            "reviewItems": corrected_document["reviewItems"],
        },
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["revision"] == 3

    confirmed = client.post(
        f"/api/v1/ocr-jobs/{job_id}/confirm",
        json={"baseRevision": 3, "documentName": "再読取後"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"] is True

    with database.session() as session:
        old_page = session.get(LedgerDocumentPage, target_page_id)
        replacement = session.query(LedgerDocumentPage).filter_by(job_id=job_id).one()
        ledger = session.get(LedgerDocument, target_document_id)
        result = session.query(OcrResult).filter_by(job_id=job_id).one()
        revisions = (
            session.query(OcrResultRevision)
            .filter_by(ocr_result_id=result.id)
            .order_by(OcrResultRevision.revision)
            .all()
        )
        assert old_page is not None and old_page.active is False
        assert replacement.active is True
        assert replacement.confirmed_at is not None
        assert ledger is not None and ledger.current_job_id == job_id
        assert [revision.source for revision in revisions] == [
            RevisionSource.OCR.value,
            RevisionSource.RECONCILIATION.value,
            RevisionSource.USER_EDIT.value,
        ]


class _FailOnceGoogleSheets:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self.calls: list[dict[str, Any]] = []
        self.fail_next = True

    def sync(self, **kwargs: Any) -> SpreadsheetSyncStatus:
        job_id = str(kwargs["job_id"])
        with self._database.session() as session:
            job = session.get(ProcessingJob, job_id)
            confirmed = session.query(ConfirmedResult).filter_by(job_id=job_id).one_or_none()
            assert job is not None and job.status == JobStatus.CONFIRMED.value
            assert confirmed is not None
        assert self._storage.exists(f"exports/{job_id}/confirmed_result.json")
        assert self._storage.exists(f"exports/{job_id}/confirmed_result.csv")
        assert self._storage.exists(f"exports/{job_id}/confirmed_result.xlsx")
        self.calls.append(dict(kwargs))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("test-only Google failure after local commit")
        return SpreadsheetSyncStatus(
            enabled=True,
            status="SYNCED",
            spreadsheet_url="https://example.invalid/sheet",
        )

    def status(
        self,
        _job_id: str,
        _document_id: str | None = None,
        _expected_subject: str | None = None,
    ) -> SpreadsheetSyncStatus:
        return SpreadsheetSyncStatus(
            enabled=True,
            status="SYNCED" if not self.fail_next else "FAILED",
            spreadsheet_url="https://example.invalid/sheet",
        )


class _RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


def test_confirm_commits_local_outputs_before_external_sync_and_remains_retryable(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    try:
        job_id, _ = _seed_review_result(database)
        google_sheets = _FailOnceGoogleSheets(database, storage)
        service = OcrResultService(
            database,
            storage,
            google_sheets=google_sheets,  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="Google failure after local commit"):
            service.confirm(job_id, base_revision=1, document_name="Phase 2 confirm")

        with database.session() as session:
            job = session.get(ProcessingJob, job_id)
            confirmed = session.query(ConfirmedResult).filter_by(job_id=job_id).one_or_none()
            ledger = session.get(LedgerDocument, job_id)
            assert job is not None and job.status == JobStatus.CONFIRMED.value
            assert confirmed is not None and confirmed.ocr_result_revision == 1
            assert ledger is not None and ledger.current_job_id == job_id
        assert len(google_sheets.calls) == 1

        retried = service.sync_spreadsheet(job_id)
        assert retried["confirmed"] is True
        assert len(google_sheets.calls) == 2
        with database.session() as session:
            job = session.get(ProcessingJob, job_id)
            confirmed = session.query(ConfirmedResult).filter_by(job_id=job_id).one_or_none()
            assert job is not None and job.status == JobStatus.CONFIRMED.value
            assert confirmed is not None and confirmed.ocr_result_revision == 1
    finally:
        database.dispose()


def test_pipeline_handler_can_run_directly_from_database_storage_and_job_id(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    clean_image = ledger_image()
    clean_bytes = encode_png(clean_image)
    canonical_height, canonical_width = clean_image.shape[:2]
    clean_path = storage.write_bytes("phase2-direct/clean.png", clean_bytes)
    config_path = "phase2-direct/template.json"
    try:
        with database.session() as session:
            source_job = ProcessingJob(
                job_type=JobType.PREPARE_TEMPLATE.value,
                status=JobStatus.CONFIRMED.value,
                request_hash="p" * 64,
                config_snapshot_json={},
            )
            session.add(source_job)
            session.flush()
            clean_artifact = JobArtifact(
                job_id=source_job.id,
                attempt_number=1,
                artifact_name="cleanTemplate",
                media_type="image/png",
                relative_path=clean_path,
                sha256=hashlib.sha256(clean_bytes).hexdigest(),
                width=canonical_width,
                height=canonical_height,
            )
            session.add(clean_artifact)
            template = Template(
                name="Phase 2 direct pipeline",
                document_type="breeding-ledger",
            )
            session.add(template)
            session.flush()
            storage.write_text(
                config_path,
                json.dumps(
                    {
                        "templateId": template.id,
                        "templateVersion": 1,
                        "name": "Phase 2 direct pipeline",
                        "canonicalWidth": canonical_width,
                        "canonicalHeight": canonical_height,
                        "registration": {
                            "preferredMethod": "ORB_HOMOGRAPHY",
                            "minFeatureMatches": 4,
                            "minInliers": 4,
                            "minInlierRatio": 0.1,
                            "maxReprojectionErrorPx": 10,
                            "minEccScore": 0.1,
                        },
                        "regions": [],
                        "fieldMappings": [
                            {
                                "mappingId": "direct_cattle_name",
                                "targetPath": "document.cattleName",
                                "label": "牛名",
                                "x": 120,
                                "y": 20,
                                "width": 240,
                                "height": 80,
                                "padding": 0,
                                "enabled": True,
                            }
                        ],
                        "marks": [],
                        "export": {"csvColumns": []},
                    },
                    ensure_ascii=False,
                ),
            )
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode=TemplateMode.REFERENCE_TEMPLATE.value,
                status=TemplateVersionStatus.ACTIVE.value,
                canonical_width=canonical_width,
                canonical_height=canonical_height,
                source_job_id=source_job.id,
                clean_template_artifact_id=clean_artifact.id,
                config_relative_path=config_path,
                quality_json={"layoutVerified": True},
                layout_json={},
            )
            session.add(version)
            session.flush()
            template.active_version_id = version.id
            session.commit()
            version_id = version.id

        queue = _RecordingQueue()
        job_service = JobService(
            settings=settings,
            database=database,
            storage=storage,
            runner=cast(JobRunner, queue),
        )
        accepted = asyncio.run(
            job_service.create_ocr_job(
                template_version_id=version_id,
                mother_individual_id="1234567890",
                upload=UploadPayload(
                    content=encode_png(ledger_image(handwriting_seed=33)),
                    declared_media_type="image/png",
                ),
                ocr_enabled=True,
                idempotency_key=None,
            )
        )
        assert accepted.status == JobStatus.QUEUED.value
        assert queue.enqueued == [accepted.id]

        extraction_service = ImageExtractionService(
            settings=settings,
            database=database,
            storage=storage,
        )
        ocr_workflow_service = OcrWorkflowService(
            settings=settings,
            database=database,
            storage=storage,
        )
        document_ocr_execution = DocumentOcrExecutionService(
            database=database,
            extraction_service=extraction_service,
            ocr_workflow_service=ocr_workflow_service,
        )
        handler = PipelineJobHandler(
            database=database,
            template_service=TemplatePreparationService(
                settings=settings,
                database=database,
                storage=storage,
            ),
            document_ocr_execution=document_ocr_execution,
        )
        handler(accepted.id)

        with database.session() as session:
            job = session.get(ProcessingJob, accepted.id)
            result = session.query(OcrResult).filter_by(job_id=accepted.id).one()
            revisions = (
                session.query(OcrResultRevision)
                .filter_by(ocr_result_id=result.id)
                .order_by(OcrResultRevision.revision)
                .all()
            )
            result_artifact = session.query(JobArtifact).filter_by(
                job_id=accepted.id,
                attempt_number=1,
                artifact_name="result",
            ).one_or_none()
            assert job is not None
            assert job.status == JobStatus.QUEUED.value
            assert result.revision == 1
            assert [revision.source for revision in revisions] == [RevisionSource.OCR.value]
            assert result_artifact is not None
    finally:
        database.dispose()
