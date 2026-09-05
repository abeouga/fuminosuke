from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    JobArtifact,
    JobEvent,
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
from app.jobs.handlers import PipelineJobHandler
from app.jobs.runner import JobRunner
from app.ocr.base import OcrProvider
from app.ocr.mock_provider import MockOcrProvider
from app.repositories.job_repository import JobRepository
from app.services.document_ocr_execution_service import DocumentOcrExecutionService
from app.services.extraction_service import ExtractionOutput, ImageExtractionService
from app.services.job_service import JobService, UploadPayload
from app.services.ocr_workflow_service import OcrWorkflowService
from app.services.template_service import TemplatePreparationService
from app.storage.local_storage import LocalStorageService
from tests.synthetic.image_factory import encode_png, ledger_image


@dataclass(frozen=True, slots=True)
class _ExecutionCase:
    settings: Settings
    database: Database
    storage: LocalStorageService
    job_id: str
    accepted_snapshot: dict[str, object]


class _RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


class _OfflineOpenAiWorkflow(OcrWorkflowService):
    def _create_provider(self, provider_name: str) -> OcrProvider:
        assert provider_name == "openai"
        return MockOcrProvider()


def _create_queued_document_job(
    settings_factory: Callable[..., Settings],
    data_dir: Path,
    *,
    handwriting_seed: int,
) -> _ExecutionCase:
    settings = settings_factory(
        data_dir=data_dir,
        ocr_provider="openai",
        openai_api_key="phase4-test-only",
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    clean_image = ledger_image()
    clean_bytes = encode_png(clean_image)
    canonical_height, canonical_width = clean_image.shape[:2]
    clean_path = storage.write_bytes("phase4/clean.png", clean_bytes)
    config_path = "phase4/template.json"

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
        template = Template(name="Phase 4 execution", document_type="breeding-ledger")
        session.add_all([clean_artifact, template])
        session.flush()
        storage.write_text(
            config_path,
            json.dumps(
                {
                    "templateId": template.id,
                    "templateVersion": 1,
                    "name": "Phase 4 execution",
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
                            "mappingId": "phase4_cattle_name",
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
        runner=queue,  # type: ignore[arg-type]
    )
    accepted = asyncio.run(
        job_service.create_ocr_job(
            template_version_id=version_id,
            mother_individual_id="1234567890",
            upload=UploadPayload(
                content=encode_png(ledger_image(handwriting_seed=handwriting_seed)),
                declared_media_type="image/png",
            ),
            ocr_enabled=True,
            idempotency_key=None,
        )
    )
    assert queue.enqueued == [accepted.id]
    return _ExecutionCase(
        settings=settings,
        database=database,
        storage=storage,
        job_id=accepted.id,
        accepted_snapshot=copy.deepcopy(accepted.config_snapshot_json),
    )


def _execution_service(case: _ExecutionCase) -> DocumentOcrExecutionService:
    extraction = ImageExtractionService(
        settings=case.settings,
        database=case.database,
        storage=case.storage,
    )
    workflow = _OfflineOpenAiWorkflow(
        settings=case.settings,
        database=case.database,
        storage=case.storage,
    )
    return DocumentOcrExecutionService(
        database=case.database,
        extraction_service=extraction,
        ocr_workflow_service=workflow,
    )


def _pipeline_handler(case: _ExecutionCase) -> PipelineJobHandler:
    return PipelineJobHandler(
        database=case.database,
        template_service=TemplatePreparationService(
            settings=case.settings,
            database=case.database,
            storage=case.storage,
        ),
        document_ocr_execution=_execution_service(case),
    )


def test_document_execution_runs_from_job_id_without_transport_or_runner(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    case = _create_queued_document_job(
        settings_factory,
        tmp_path / "direct-execution",
        handwriting_seed=51,
    )
    try:
        _execution_service(case).execute(case.job_id)

        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            result = session.scalar(select(OcrResult).where(OcrResult.job_id == case.job_id))
            revisions = (
                session.scalars(
                    select(OcrResultRevision).where(
                        OcrResultRevision.ocr_result_id == result.id
                    )
                ).all()
                if result is not None
                else []
            )
            artifacts = {
                artifact.artifact_name: artifact
                for artifact in session.scalars(
                    select(JobArtifact).where(JobArtifact.job_id == case.job_id)
                )
            }
            assert job is not None
            assert result is not None
            assert job.status == JobStatus.QUEUED.value
            assert job.config_snapshot_json == case.accepted_snapshot
            assert result.provider == "mock"
            assert result.api_call_count == 0
            assert result.revision == 1
            assert [revision.source for revision in revisions] == [RevisionSource.OCR.value]
            assert {"mappedCells", "result"} <= artifacts.keys()

        mapped_cells = json.loads(
            case.storage.read_bytes(artifacts["mappedCells"].relative_path)
        )
        final_result = json.loads(case.storage.read_bytes(artifacts["result"].relative_path))
        mapped_cell = mapped_cells["cells"][0]
        assert mapped_cells["fieldMappingHash"] == case.accepted_snapshot["fieldMappingHash"]
        assert mapped_cell["mappingId"] == "phase4_cattle_name"
        assert mapped_cell["openAiMappingId"] == "phase4_cattle_name"
        assert mapped_cell["targetPath"] == "document.cattleName"
        assert mapped_cell["boundingBox"] == {
            "x": 120,
            "y": 20,
            "width": 240,
            "height": 80,
        }
        assert mapped_cell["alignedCellArtifact"] == "fieldCrop.phase4_cattle_name.alignedColor"
        assert mapped_cell["alignedCellSha256"] == artifacts[
            "fieldCrop.phase4_cattle_name.alignedColor"
        ].sha256
        assert final_result["jobId"] == case.job_id
        assert final_result["revision"] == 1
    finally:
        case.database.dispose()


class _CancelAfterExtraction:
    def __init__(self, database: Database) -> None:
        self._database = database
        self.calls: list[str] = []

    def process(self, job_id: str) -> ExtractionOutput:
        self.calls.append(job_id)
        with self._database.session() as session:
            JobRepository(session).update(job_id, cancel_requested=True)
        return cast(ExtractionOutput, object())


class _RecordingWorkflow:
    def __init__(self) -> None:
        self.calls: list[ExtractionOutput] = []

    def process(self, extraction: ExtractionOutput) -> None:
        self.calls.append(extraction)


def test_document_execution_preserves_the_post_extraction_cancel_boundary(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    case = _create_queued_document_job(
        settings_factory,
        tmp_path / "post-extraction-cancel",
        handwriting_seed=52,
    )
    extraction = _CancelAfterExtraction(case.database)
    workflow = _RecordingWorkflow()
    execution = DocumentOcrExecutionService(
        database=case.database,
        extraction_service=cast(ImageExtractionService, extraction),
        ocr_workflow_service=cast(OcrWorkflowService, workflow),
    )
    try:
        execution.execute(case.job_id)

        assert extraction.calls == [case.job_id]
        assert workflow.calls == []
        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            assert job is not None
            assert job.status == JobStatus.QUEUED.value
            assert job.cancel_requested is True
    finally:
        case.database.dispose()


class _RecordingDocumentExecution:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, job_id: str) -> None:
        self.calls.append(job_id)


class _RecordingTemplatePreparation:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def process(self, job_id: str) -> str:
        self.calls.append(job_id)
        return job_id


def test_pipeline_handler_delegates_only_document_jobs_to_execution_boundary(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    settings = settings_factory(data_dir=tmp_path / "handler-delegation")
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    document_execution = _RecordingDocumentExecution()
    template_preparation = _RecordingTemplatePreparation()
    try:
        with database.session() as session:
            repository = JobRepository(session)
            document_job = repository.create(
                job_type=JobType.EXTRACT_DOCUMENT,
                request_hash="d" * 64,
                config_snapshot={},
                status=JobStatus.QUEUED,
            )
            template_job = repository.create(
                job_type=JobType.PREPARE_TEMPLATE,
                request_hash="t" * 64,
                config_snapshot={},
                status=JobStatus.QUEUED,
                mode=TemplateMode.REFERENCE_TEMPLATE,
            )
            cancelled_job = repository.create(
                job_type=JobType.EXTRACT_DOCUMENT,
                request_hash="c" * 64,
                config_snapshot={},
                status=JobStatus.QUEUED,
            )
            repository.update(cancelled_job.id, cancel_requested=True)

        handler = PipelineJobHandler(
            database=database,
            template_service=cast(
                TemplatePreparationService,
                template_preparation,
            ),
            document_ocr_execution=cast(
                DocumentOcrExecutionService,
                document_execution,
            ),
        )

        handler(document_job.id)
        handler(template_job.id)
        handler(cancelled_job.id)

        assert document_execution.calls == [document_job.id]
        assert template_preparation.calls == [template_job.id]
        assert not hasattr(handler, "_extraction_service")
        assert not hasattr(handler, "_ocr_workflow")
    finally:
        database.dispose()


def test_job_runner_owns_document_execution_begin_and_finish_transitions(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    case = _create_queued_document_job(
        settings_factory,
        tmp_path / "runner-lifecycle",
        handwriting_seed=53,
    )
    runner = JobRunner(
        database=case.database,
        settings=case.settings,
        storage=case.storage,
        handler=_pipeline_handler(case),
        worker_count=1,
    )
    try:
        asyncio.run(runner._execute(case.job_id))

        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            result = session.scalar(select(OcrResult).where(OcrResult.job_id == case.job_id))
            revisions = (
                session.scalars(
                    select(OcrResultRevision).where(
                        OcrResultRevision.ocr_result_id == result.id
                    )
                ).all()
                if result is not None
                else []
            )
            artifact_names = set(
                session.scalars(
                    select(JobArtifact.artifact_name).where(
                        JobArtifact.job_id == case.job_id
                    )
                )
            )
            event_codes = list(
                session.scalars(
                    select(JobEvent.event_code)
                    .where(JobEvent.job_id == case.job_id)
                    .order_by(JobEvent.created_at, JobEvent.id)
                )
            )
            assert job is not None
            assert result is not None
            assert job.status == JobStatus.REVIEW_REQUIRED.value
            assert job.progress_stage == "READY_FOR_REVIEW"
            assert job.progress_percent == 100
            assert job.config_snapshot_json == case.accepted_snapshot
            assert result.revision == 1
            assert [revision.source for revision in revisions] == [RevisionSource.OCR.value]
            assert {"mappedCells", "result"} <= artifact_names
            assert "JOB_PROCESSING_STARTED" in event_codes
            assert event_codes[-1] == "JOB_READY_FOR_REVIEW"
    finally:
        case.database.dispose()


def test_job_runner_cancels_before_document_execution_begins(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    case = _create_queued_document_job(
        settings_factory,
        tmp_path / "runner-cancel-before-begin",
        handwriting_seed=54,
    )
    with case.database.session() as session:
        JobRepository(session).update(case.job_id, cancel_requested=True)
    runner = JobRunner(
        database=case.database,
        settings=case.settings,
        storage=case.storage,
        handler=_pipeline_handler(case),
        worker_count=1,
    )
    try:
        asyncio.run(runner._execute(case.job_id))

        with case.database.session() as session:
            job = session.get(ProcessingJob, case.job_id)
            result = session.scalar(select(OcrResult).where(OcrResult.job_id == case.job_id))
            result_artifact = session.scalar(
                select(JobArtifact).where(
                    JobArtifact.job_id == case.job_id,
                    JobArtifact.artifact_name == "result",
                )
            )
            assert job is not None
            assert job.status == JobStatus.CANCELLED.value
            assert result is None
            assert result_artifact is None
    finally:
        case.database.dispose()
