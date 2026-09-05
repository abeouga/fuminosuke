from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import (
    JobArtifact,
    JobInput,
    LedgerDocument,
    LedgerDocumentPage,
    ProcessingJob,
    Template,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.jobs.runner import JobRunner
from app.main import create_app
from app.services.job_service import JobService, UploadPayload
from app.storage.local_storage import LocalStorageService
from tests.synthetic.image_factory import encode_png, ledger_image

_TEMPLATE_ID = "30000000-0000-0000-0000-000000000001"
_TEMPLATE_VERSION_ID = "30000000-0000-0000-0000-000000000002"
_SOURCE_JOB_ID = "30000000-0000-0000-0000-000000000003"
_SOURCE_WORKBOOK_ARTIFACT_ID = "30000000-0000-0000-0000-000000000004"
_CLEAN_TEMPLATE_ARTIFACT_ID = "30000000-0000-0000-0000-000000000005"
_TARGET_DOCUMENT_ID = "30000000-0000-0000-0000-000000000006"
_TARGET_PAGE_ID = "30000000-0000-0000-0000-000000000007"
_PREVIOUS_PAGE_JOB_ID = "30000000-0000-0000-0000-000000000008"
_IDEMPOTENCY_KEY = "phase3-submission-contract"
_MOTHER_INDIVIDUAL_ID = "1234567890"


@dataclass(frozen=True, slots=True)
class _Environment:
    settings: Settings
    database: Database
    storage: LocalStorageService


class _RecordingRunner:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


def _source_workbook_bytes() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "様式"
    worksheet["A1"] = "Phase 3 frozen source"
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _template_config_bytes(*, width: int, height: int) -> bytes:
    payload = {
        "templateId": _TEMPLATE_ID,
        "templateVersion": 1,
        "name": "Phase 3 submission template",
        "canonicalWidth": width,
        "canonicalHeight": height,
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
                "mappingId": "phase3_cattle_name",
                "targetPath": "document.cattleName",
                "label": "牛名",
                "x": 120,
                "y": 20,
                "width": 240,
                "height": 80,
                "padding": 7,
                "enabled": True,
            }
        ],
        "marks": [],
        "export": {"csvColumns": []},
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _prepare_environment(
    settings: Settings,
    *,
    clean_template_bytes: bytes,
    source_workbook_bytes: bytes,
) -> _Environment:
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()

    clean_path = storage.write_bytes("phase3-seed/clean.png", clean_template_bytes)
    workbook_path = storage.write_bytes("phase3-seed/source.xlsx", source_workbook_bytes)
    clean_image = ledger_image()
    canonical_height, canonical_width = clean_image.shape[:2]
    config_path = storage.write_bytes(
        "phase3-seed/template.json",
        _template_config_bytes(width=canonical_width, height=canonical_height),
    )

    with database.session() as session:
        source_job = ProcessingJob(
            id=_SOURCE_JOB_ID,
            job_type=JobType.PREPARE_TEMPLATE.value,
            status=JobStatus.CONFIRMED.value,
            request_hash="s" * 64,
            config_snapshot_json={},
        )
        previous_page_job = ProcessingJob(
            id=_PREVIOUS_PAGE_JOB_ID,
            job_type=JobType.EXTRACT_DOCUMENT.value,
            status=JobStatus.CONFIRMED.value,
            request_hash="p" * 64,
            config_snapshot_json={},
        )
        template = Template(
            id=_TEMPLATE_ID,
            name="Phase 3 submission template",
            document_type="spreadsheet",
        )
        session.add_all([source_job, previous_page_job, template])
        session.flush()

        source_artifact = JobArtifact(
            id=_SOURCE_WORKBOOK_ARTIFACT_ID,
            job_id=source_job.id,
            attempt_number=1,
            artifact_name="sourceWorkbook",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            relative_path=workbook_path,
            sha256=hashlib.sha256(source_workbook_bytes).hexdigest(),
        )
        clean_artifact = JobArtifact(
            id=_CLEAN_TEMPLATE_ARTIFACT_ID,
            job_id=source_job.id,
            attempt_number=1,
            artifact_name="cleanTemplate",
            media_type="image/png",
            relative_path=clean_path,
            sha256=hashlib.sha256(clean_template_bytes).hexdigest(),
            width=canonical_width,
            height=canonical_height,
        )
        session.add_all([source_artifact, clean_artifact])
        session.flush()

        version = TemplateVersion(
            id=_TEMPLATE_VERSION_ID,
            template_id=template.id,
            version_number=1,
            mode=TemplateMode.SPREADSHEET_TEMPLATE.value,
            status=TemplateVersionStatus.ACTIVE.value,
            canonical_width=canonical_width,
            canonical_height=canonical_height,
            source_job_id=source_job.id,
            clean_template_artifact_id=clean_artifact.id,
            config_relative_path=config_path,
            quality_json={
                "layoutVerified": True,
                "sourceSheetName": "様式",
                "sourceFileName": "phase3-source.xlsx",
            },
            layout_json={
                "layoutSchemaVersion": 3,
                "canonicalWidth": canonical_width,
                "canonicalHeight": canonical_height,
                "cells": [],
                "cellMerges": [],
            },
        )
        document = LedgerDocument(
            id=_TARGET_DOCUMENT_ID,
            template_id=template.id,
            individual_id=_MOTHER_INDIVIDUAL_ID,
            current_job_id=previous_page_job.id,
            current_document_json={},
        )
        session.add_all([version, document])
        session.flush()
        template.active_version_id = version.id
        session.add(
            LedgerDocumentPage(
                id=_TARGET_PAGE_ID,
                ledger_document_id=document.id,
                job_id=previous_page_job.id,
                page_number=1,
                relation_type="INITIAL",
                active=True,
                page_content_json={},
            )
        )
        session.commit()

    return _Environment(settings=settings, database=database, storage=storage)


def _new_environment(
    settings_factory: Callable[..., Settings],
    data_dir: Path,
    *,
    clean_template_bytes: bytes,
    source_workbook_bytes: bytes,
) -> _Environment:
    settings = settings_factory(data_dir=data_dir, ocr_provider="mock")
    return _prepare_environment(
        settings,
        clean_template_bytes=clean_template_bytes,
        source_workbook_bytes=source_workbook_bytes,
    )


async def _submit_direct(
    environment: _Environment,
    runner: _RecordingRunner,
    upload_bytes: bytes,
    *,
    idempotency_key: str = _IDEMPOTENCY_KEY,
) -> ProcessingJob:
    service = JobService(
        settings=environment.settings,
        database=environment.database,
        storage=environment.storage,
        runner=cast(JobRunner, runner),
    )
    return await service.create_ocr_job(
        template_version_id=_TEMPLATE_VERSION_ID,
        mother_individual_id=_MOTHER_INDIVIDUAL_ID,
        upload=UploadPayload(content=upload_bytes, declared_media_type="image/png"),
        ocr_enabled=True,
        send_full_document_to_openai=True,
        document_action="RESCAN_PAGE",
        target_document_id=_TARGET_DOCUMENT_ID,
        target_page_id=_TARGET_PAGE_ID,
        idempotency_key=idempotency_key,
    )


def _normalized_submission_contract(
    environment: _Environment,
    job_id: str,
) -> dict[str, Any]:
    with environment.database.session() as session:
        job = session.get(ProcessingJob, job_id)
        job_input = session.scalar(select(JobInput).where(JobInput.job_id == job_id))
        assert job is not None
        assert job_input is not None

        config_snapshot = copy.deepcopy(job.config_snapshot_json)
        template_snapshot = config_snapshot["templateSnapshot"]
        assert isinstance(template_snapshot, dict)
        snapshot_path = template_snapshot["cleanTemplatePath"]
        assert snapshot_path == f"uploads/{job_id}/template_snapshot/clean_template.png"
        template_snapshot["cleanTemplatePath"] = (
            "uploads/<job-id>/template_snapshot/clean_template.png"
        )

        assert job_input.relative_path == f"uploads/{job_id}/original.png"
        manifest = json.loads(
            environment.storage.read_bytes(f"uploads/{job_id}/manifest.json")
        )
        assert manifest["jobId"] == job_id
        manifest["jobId"] = "<job-id>"
        manifest["inputs"][0]["relativePath"] = "uploads/<job-id>/original.png"

        return {
            "jobType": job.job_type,
            "status": job.status,
            "attemptNumber": job.attempt_number,
            "idempotencyKey": job.idempotency_key,
            "requestHash": job.request_hash,
            "imageSha256": job.image_sha256,
            "configSnapshot": config_snapshot,
            "templateSnapshotBytes": job.template_snapshot_bytes,
            "input": {
                "index": job_input.input_index,
                "mediaType": job_input.media_type,
                "format": job_input.format,
                "width": job_input.width,
                "height": job_input.height,
                "fileBytes": job_input.file_bytes,
                "sha256": job_input.sha256,
                "relativePath": "uploads/<job-id>/original.png",
                "contentBytes": job_input.content_bytes,
            },
            "storedOriginal": environment.storage.read_bytes(job_input.relative_path),
            "storedTemplateSnapshot": environment.storage.read_bytes(snapshot_path),
            "manifest": manifest,
        }


def test_job_service_accepts_a_complete_ocr_submission_without_fastapi_objects(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    clean_template_bytes = encode_png(ledger_image())
    source_workbook_bytes = _source_workbook_bytes()
    upload_bytes = encode_png(ledger_image(handwriting_seed=41))
    environment = _new_environment(
        settings_factory,
        tmp_path / "direct-complete",
        clean_template_bytes=clean_template_bytes,
        source_workbook_bytes=source_workbook_bytes,
    )
    runner = _RecordingRunner()
    try:
        accepted = asyncio.run(_submit_direct(environment, runner, upload_bytes))
        contract = _normalized_submission_contract(environment, accepted.id)
        snapshot = contract["configSnapshot"]
        template_snapshot = snapshot["templateSnapshot"]
        spreadsheet_snapshot = snapshot["spreadsheetOutput"]

        assert contract["jobType"] == JobType.EXTRACT_DOCUMENT.value
        assert contract["status"] == JobStatus.QUEUED.value
        assert contract["attemptNumber"] == 1
        assert contract["idempotencyKey"] == _IDEMPOTENCY_KEY
        assert len(contract["requestHash"]) == 64
        assert set(contract["requestHash"]) <= set("0123456789abcdef")
        assert {"subject", "owner"}.isdisjoint(ProcessingJob.__table__.columns.keys())

        assert snapshot["templateVersionId"] == _TEMPLATE_VERSION_ID
        assert snapshot["templateId"] == _TEMPLATE_ID
        assert template_snapshot["cleanTemplateSha256"] == hashlib.sha256(
            clean_template_bytes
        ).hexdigest()
        assert contract["templateSnapshotBytes"] == clean_template_bytes
        assert snapshot["fieldMappings"] == [
            {
                "mappingId": "phase3_cattle_name",
                "targetPath": "document.cattleName",
                "label": "牛名",
                "x": 120,
                "y": 20,
                "width": 240,
                "height": 80,
                "padding": 0,
                "enabled": True,
                "modelDetail": "high",
                "printedLabelText": "",
                "mappingType": "text",
                "markRecognitionMode": "automatic",
                "markSelectionMode": "multiple",
                "markOptions": [],
                "compositeSubfields": [],
            }
        ]
        assert len(snapshot["fieldMappingHash"]) == 64
        assert snapshot["fieldMappingSource"] == "TEMPLATE_VERSION_SNAPSHOT"
        assert snapshot["reviewStructure"]["schemaVersion"] == 1
        assert snapshot["ocrEnabled"] is True
        assert snapshot["ocrProvider"] == "mock"
        assert snapshot["sendFullDocumentToOpenAi"] is False
        assert snapshot["schemaVersion"] == environment.settings.ocr.schema_version
        assert snapshot["promptVersion"] == environment.settings.ocr.prompt_version
        assert snapshot["templateCandidatePools"] == []
        assert snapshot["templateCandidatePoolsHash"] is None
        assert snapshot["documentAction"] == "RESCAN_PAGE"
        assert snapshot["targetDocumentId"] == _TARGET_DOCUMENT_ID
        assert snapshot["targetPageId"] == _TARGET_PAGE_ID
        assert spreadsheet_snapshot["templateVersionId"] == _TEMPLATE_VERSION_ID
        assert spreadsheet_snapshot["sourceWorkbook"] == {
            "artifactId": _SOURCE_WORKBOOK_ARTIFACT_ID,
            "sourceJobId": _SOURCE_JOB_ID,
            "sha256": hashlib.sha256(source_workbook_bytes).hexdigest(),
        }

        assert contract["imageSha256"] == hashlib.sha256(upload_bytes).hexdigest()
        assert contract["input"]["sha256"] == contract["imageSha256"]
        assert contract["input"]["mediaType"] == "image/png"
        assert contract["input"]["format"] == "png"
        assert contract["input"]["width"] > 0
        assert contract["input"]["height"] > 0
        assert contract["input"]["contentBytes"] == upload_bytes
        assert contract["storedOriginal"] == upload_bytes
        assert contract["storedTemplateSnapshot"] == clean_template_bytes
        assert contract["manifest"]["inputs"][0]["sha256"] == contract["imageSha256"]
        assert runner.enqueued == [accepted.id]
    finally:
        environment.database.dispose()


def test_http_and_direct_submission_are_transport_independent_equivalents(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean_template_bytes = encode_png(ledger_image())
    source_workbook_bytes = _source_workbook_bytes()
    upload_bytes = encode_png(ledger_image(handwriting_seed=42))
    direct = _new_environment(
        settings_factory,
        tmp_path / "equivalence-direct",
        clean_template_bytes=clean_template_bytes,
        source_workbook_bytes=source_workbook_bytes,
    )
    direct_runner = _RecordingRunner()
    http_settings = settings_factory(
        data_dir=tmp_path / "equivalence-http",
        ocr_provider="mock",
    )
    http_seed = _prepare_environment(
        http_settings,
        clean_template_bytes=clean_template_bytes,
        source_workbook_bytes=source_workbook_bytes,
    )
    http_seed.database.dispose()
    try:
        direct_job = asyncio.run(_submit_direct(direct, direct_runner, upload_bytes))
        direct_contract = _normalized_submission_contract(direct, direct_job.id)

        http_enqueued: list[str] = []

        async def record_http_enqueue(job_id: str) -> None:
            http_enqueued.append(job_id)

        with TestClient(create_app(http_settings)) as client:
            monkeypatch.setattr(client.app.state.job_runner, "enqueue", record_http_enqueue)
            response = client.post(
                "/api/v1/ocr-jobs",
                data={
                    "templateVersionId": _TEMPLATE_VERSION_ID,
                    "motherIndividualId": _MOTHER_INDIVIDUAL_ID,
                    "ocrEnabled": "true",
                    "sendFullDocumentToOpenAi": "true",
                    "documentAction": "RESCAN_PAGE",
                    "targetDocumentId": _TARGET_DOCUMENT_ID,
                    "targetPageId": _TARGET_PAGE_ID,
                },
                files={"file": ("transport-name.png", upload_bytes, "image/png")},
                headers={"Idempotency-Key": _IDEMPOTENCY_KEY},
            )
            assert response.status_code == 202
            http_job_id = response.json()["jobId"]
            http_environment = _Environment(
                settings=http_settings,
                database=client.app.state.database,
                storage=client.app.state.storage,
            )
            http_contract = _normalized_submission_contract(http_environment, http_job_id)

            assert http_contract == direct_contract
            assert direct_runner.enqueued == [direct_job.id]
            assert http_enqueued == [http_job_id]
            assert response.json()["status"] == JobStatus.QUEUED.value
    finally:
        direct.database.dispose()


def test_direct_submission_preserves_idempotency_and_rejects_key_reuse(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean_template_bytes = encode_png(ledger_image())
    environment = _new_environment(
        settings_factory,
        tmp_path / "direct-idempotency",
        clean_template_bytes=clean_template_bytes,
        source_workbook_bytes=_source_workbook_bytes(),
    )
    upload_bytes = encode_png(ledger_image(handwriting_seed=43))
    runner = _RecordingRunner()
    service = JobService(
        settings=environment.settings,
        database=environment.database,
        storage=environment.storage,
        runner=cast(JobRunner, runner),
    )
    try:
        first = asyncio.run(_submit_direct(environment, runner, upload_bytes))

        def unexpected_persistence(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("idempotent replay must not persist submission data again")

        monkeypatch.setattr(service, "_persist_template_snapshot", unexpected_persistence)
        monkeypatch.setattr(service, "_persist_inputs", unexpected_persistence)
        second = asyncio.run(
            service.create_ocr_job(
                template_version_id=_TEMPLATE_VERSION_ID,
                mother_individual_id=_MOTHER_INDIVIDUAL_ID,
                upload=UploadPayload(
                    content=upload_bytes,
                    declared_media_type="image/png",
                ),
                ocr_enabled=True,
                send_full_document_to_openai=True,
                document_action="RESCAN_PAGE",
                target_document_id=_TARGET_DOCUMENT_ID,
                target_page_id=_TARGET_PAGE_ID,
                idempotency_key=_IDEMPOTENCY_KEY,
            )
        )

        assert second.id == first.id
        assert runner.enqueued == [first.id]
        with environment.database.session() as session:
            jobs = session.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.idempotency_key == _IDEMPOTENCY_KEY
                )
            ).all()
            input_count = session.scalar(
                select(func.count(JobInput.id)).where(JobInput.job_id == first.id)
            )
            assert [job.id for job in jobs] == [first.id]
            assert input_count == 1

        with pytest.raises(AppError) as conflict:
            asyncio.run(
                service.create_ocr_job(
                    template_version_id=_TEMPLATE_VERSION_ID,
                    mother_individual_id=_MOTHER_INDIVIDUAL_ID,
                    upload=UploadPayload(
                        content=encode_png(ledger_image(handwriting_seed=44)),
                        declared_media_type="image/png",
                    ),
                    ocr_enabled=True,
                    send_full_document_to_openai=True,
                    document_action="RESCAN_PAGE",
                    target_document_id=_TARGET_DOCUMENT_ID,
                    target_page_id=_TARGET_PAGE_ID,
                    idempotency_key=_IDEMPOTENCY_KEY,
                )
            )
        assert conflict.value.status == 409
        assert conflict.value.code == "IDEMPOTENCY_KEY_CONFLICT"
        assert runner.enqueued == [first.id]
    finally:
        environment.database.dispose()


@pytest.mark.parametrize("failure_stage", ["template_snapshot", "input_manifest"])
def test_direct_submission_compensates_snapshot_and_input_failures(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    environment = _new_environment(
        settings_factory,
        tmp_path / failure_stage,
        clean_template_bytes=encode_png(ledger_image()),
        source_workbook_bytes=_source_workbook_bytes(),
    )
    runner = _RecordingRunner()
    failed_job_ids: list[str] = []
    original_write_bytes = environment.storage.write_bytes
    original_write_text = environment.storage.write_text

    def fail_snapshot_write(relative_path: str | Path, content: bytes) -> str:
        stored_path = original_write_bytes(relative_path, content)
        normalized = Path(relative_path).as_posix()
        if normalized.endswith("/template_snapshot/clean_template.png"):
            failed_job_ids.append(normalized.split("/")[1])
            raise OSError("simulated template snapshot persistence failure")
        return stored_path

    def fail_manifest_write(
        relative_path: str | Path,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> str:
        stored_path = original_write_text(relative_path, content, encoding=encoding)
        normalized = Path(relative_path).as_posix()
        if normalized.endswith("/manifest.json"):
            failed_job_ids.append(normalized.split("/")[1])
            raise OSError("simulated input manifest persistence failure")
        return stored_path

    if failure_stage == "template_snapshot":
        monkeypatch.setattr(environment.storage, "write_bytes", fail_snapshot_write)
    else:
        monkeypatch.setattr(environment.storage, "write_text", fail_manifest_write)

    try:
        with pytest.raises(OSError, match="simulated"):
            asyncio.run(
                _submit_direct(
                    environment,
                    runner,
                    encode_png(ledger_image(handwriting_seed=45)),
                    idempotency_key=f"phase3-failure-{failure_stage}",
                )
            )

        assert len(failed_job_ids) == 1
        failed_job_id = failed_job_ids[0]
        with environment.database.session() as session:
            assert session.get(ProcessingJob, failed_job_id) is None
            assert session.scalar(
                select(func.count(JobInput.id)).where(JobInput.job_id == failed_job_id)
            ) == 0
        assert not environment.storage.exists(f"uploads/{failed_job_id}")
        assert runner.enqueued == []
    finally:
        environment.database.dispose()
