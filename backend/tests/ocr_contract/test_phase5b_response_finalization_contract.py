from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import JobArtifact, OcrResult
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.ocr_models import ReadingStatus
from app.ocr.base import OcrProvider, OcrProviderRequest, OcrProviderResponse
from app.ocr.deterministic_mapper import build_mapped_document
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem
from app.repositories.job_repository import JobRepository
from app.services.extraction_service import ExtractionOutput
from app.services.ocr_request_preparation_service import OcrRequestPreparer
from app.services.ocr_response_finalization_service import OcrResponseFinalizer
from app.services.ocr_workflow_service import OcrWorkflowService
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator
from tests.ocr_contract.test_phase5a_request_preparation_contract import (
    EXPECTED_MAPPED_CELL_ORDER,
    EXPECTED_MAPPED_CELL_SHA,
    EXPECTED_SENT_MAPPING_IDS,
    _extraction,
    _snapshot,
)

EXPECTED_CANONICAL_SHA256 = {
    "document": "385f7dae914f632c867e83283c569407bc92c7e8e32e1369960c2b0c08fee566",
    "validation": "9e13c18d651db6af7422f7a2f91f0a672146cdee1f1cabb2673c3fbca933e47d",
    "mappedCells": "fe6442fc78bada774d54c5811c55d731ef5d18b7d66d9e15bf58899a6879a090",
    "diagnostics": "c655bd178e9a2853e33e34360e77b6431d230d4070dbf424f307e66f3a9abba0",
    "resultArtifact": "6796e88dc8da41f4bbcc81803177e4ca9c752b708ecd0e12839194eafe6d18a0",
}


def _canonical_sha256(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class _Phase5BProvider:
    def __init__(self, trace: list[str]) -> None:
        self._trace = trace

    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse:
        self._trace.append("provider.extract")
        values = {
            "identity": TranscriptionItem(
                mapping_id="identity",
                raw="0123456789",
                status=ReadingStatus.READABLE,
                matched_candidate="0123456789",
            ),
            "sire-0": TranscriptionItem(
                mapping_id="sire-0",
                raw="Sire A",
                status=ReadingStatus.READABLE,
                matched_candidate="Sire A",
            ),
            "price-0": TranscriptionItem(
                mapping_id="price-0",
                raw="12,345円",
                status=ReadingStatus.READABLE,
            ),
            "notes-0": TranscriptionItem(
                mapping_id="notes-0",
                raw="♂ 出荷",
                status=ReadingStatus.READABLE,
                matched_candidate="♂",
            ),
            "price-1": TranscriptionItem(
                mapping_id="price-1",
                raw=None,
                status=ReadingStatus.BLANK,
            ),
            "calving-date": TranscriptionItem(
                mapping_id="calving-date",
                raw="99 13 40",
                status=ReadingStatus.READABLE,
            ),
            "mark-openai": TranscriptionItem(
                mapping_id="mark-openai",
                raw="Yes",
                status=ReadingStatus.READABLE,
            ),
        }
        # Reverse the response order deliberately. Existing deterministic mapping
        # must continue to use mapping identity rather than array position.
        items = [values[region.region_id] for region in reversed(request.regions)]
        manifest = [
            {
                "requestOrder": index,
                "compactId": f"c{index}",
                "originalMappingId": region.region_id,
                "targetPath": region.target_path,
                "sourceRegionId": region.source_region_id,
                "imageSha256": hashlib.sha256(region.image_png).hexdigest(),
                "associationToken": f"a{index:05d}",
            }
            for index, region in enumerate(request.regions)
        ]
        return OcrProviderResponse(
            document=build_mapped_document(request, TranscriptionBatch(items=items)),
            provider="openai",
            model_id="phase5b-model",
            prompt_version="phase5b-prompt",
            api_call_count=1,
            input_tokens=321,
            output_tokens=123,
            estimated_cost=Decimal("0.00042"),
            request_id="phase5b-request",
            duration_ms=123.5,
            diagnostics=(
                {
                    "schemaVersion": 1,
                    "callIndex": 1,
                    "batchNumber": 1,
                    "requestManifest": manifest,
                    "rawItems": [item.model_dump(mode="json", by_alias=True) for item in items],
                    "expandedItems": [
                        item.model_dump(mode="json", by_alias=True) for item in items
                    ],
                },
            ),
        )


class _Phase5BWorkflow(OcrWorkflowService):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.trace: list[str] = []

    def _create_provider(self, provider_name: str) -> OcrProvider:
        assert provider_name == "openai"
        return _Phase5BProvider(self.trace)

    def _progress(self, job_id: str, stage: str, percent: int) -> None:
        self.trace.append(stage)
        super()._progress(job_id, stage, percent)


def _artifact_payload(
    storage: LocalStorageService,
    artifacts: dict[str, JobArtifact],
    name: str,
) -> object:
    return json.loads(storage.read_bytes(artifacts[name].relative_path))


def test_response_finalizer_matches_the_pre_extraction_contract() -> None:
    extraction = _extraction("phase5b-direct-finalizer")
    extraction.quality = {
        "edgeOverlap": 0.6,
        "handwritingCoverageRatio": 0.3,
    }
    snapshot = _snapshot(send_full_document=False)
    snapshot["motherIndividualId"] = "9876543210"
    prepared = OcrRequestPreparer().prepare(
        extraction=extraction,
        job_snapshot=snapshot,
        provider_name="openai",
        should_cancel=lambda: False,
    )
    provider_response = asyncio.run(_Phase5BProvider([]).extract(prepared.request))

    finalized = OcrResponseFinalizer(DeterministicValidator()).finalize(
        prepared=prepared,
        provider_response=provider_response,
        extraction=extraction,
        job_snapshot=snapshot,
        provider_name="openai",
    )

    document = finalized.response.document.model_dump(mode="json", by_alias=True)
    validation = finalized.validation_report.to_dict()
    diagnostics = finalized.diagnostics_payload
    assert diagnostics is not None
    stable_diagnostics = diagnostics | {"jobId": "<job-id>"}
    result_artifact = {
        "jobId": "<job-id>",
        "revision": 1,
        "result": document,
        "validation": validation,
        "quality": extraction.quality,
    }
    assert {
        "document": _canonical_sha256(document),
        "validation": _canonical_sha256(validation),
        "mappedCells": _canonical_sha256(finalized.mapped_cells_payload),
        "diagnostics": _canonical_sha256(stable_diagnostics),
        "resultArtifact": _canonical_sha256(result_artifact),
    } == EXPECTED_CANONICAL_SHA256
    assert finalized.configured_paths == {
        "document.unifiedEarTag",
        "saleCalves.0.sireName",
        "saleCalves.0.priceYen",
        "saleCalves.0.notes",
        "saleCalves.1.priceYen",
        "calvings.0.parity",
        "calvings.0.calvingDate",
        "customFields.localMark",
        "customFields.manualMark",
        "customFields.openAiMark",
    }


def test_existing_response_finalization_is_canonical(
    settings_factory: Callable[..., Settings],
    tmp_path: Path,
) -> None:
    settings = settings_factory(
        data_dir=tmp_path,
        ocr_provider="openai",
        openai_api_key="phase5b-test-only",
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    snapshot = _snapshot(send_full_document=False)
    snapshot["motherIndividualId"] = "9876543210"
    try:
        with database.session() as session:
            job = JobRepository(session).create(
                job_type=JobType.EXTRACT_DOCUMENT,
                request_hash="b" * 64,
                config_snapshot=snapshot,
                status=JobStatus.QUEUED,
            )
        extraction: ExtractionOutput = _extraction(job.id)
        extraction.quality = {
            "edgeOverlap": 0.6,
            "handwritingCoverageRatio": 0.3,
        }
        workflow = _Phase5BWorkflow(
            settings=settings,
            database=database,
            storage=storage,
        )

        workflow.process(extraction)

        with database.session() as session:
            result = session.scalar(select(OcrResult).where(OcrResult.job_id == job.id))
            assert result is not None
            artifact_rows = list(
                session.scalars(select(JobArtifact).where(JobArtifact.job_id == job.id))
            )
            artifacts = {artifact.artifact_name: artifact for artifact in artifact_rows}

        assert set(artifacts) == {"mappedCells", "openAiMappingDiagnostics", "result"}
        mapped_cells = _artifact_payload(storage, artifacts, "mappedCells")
        diagnostics = _artifact_payload(storage, artifacts, "openAiMappingDiagnostics")
        result_artifact = _artifact_payload(storage, artifacts, "result")
        assert isinstance(mapped_cells, dict)
        assert isinstance(diagnostics, dict)
        assert isinstance(result_artifact, dict)
        assert diagnostics["jobId"] == job.id
        assert result_artifact["jobId"] == job.id
        stable_diagnostics = diagnostics | {"jobId": "<job-id>"}
        stable_result_artifact = result_artifact | {"jobId": "<job-id>"}

        actual_hashes = {
            "document": _canonical_sha256(result.raw_candidate_json),
            "validation": _canonical_sha256(result.validation_json),
            "mappedCells": _canonical_sha256(mapped_cells),
            "diagnostics": _canonical_sha256(stable_diagnostics),
            "resultArtifact": _canonical_sha256(stable_result_artifact),
        }
        assert actual_hashes == EXPECTED_CANONICAL_SHA256

        assert result.current_corrected_json == result.raw_candidate_json
        assert result.revision == 1
        assert result.provider == "openai"
        assert result.model_id == "phase5b-model"
        assert result.prompt_version == "phase5b-prompt"
        assert result.api_call_count == 1
        assert result.input_tokens == 321
        assert result.output_tokens == 123
        assert result.validation_json == {
            "valid": False,
            "issues": [
                {
                    "code": "LOW_REGISTRATION_CONFIDENCE",
                    "path": "processingMetadata",
                    "message": "位置合わせ品質が低いため確認が必要です。",
                    "severity": "error",
                    "source_region_id": None,
                },
                {
                    "code": "MASK_COVERAGE_HIGH",
                    "path": "processingMetadata",
                    "message": "手書きマスク面積が大きすぎます。",
                    "severity": "warning",
                    "source_region_id": None,
                },
            ],
        }

        document = result.raw_candidate_json
        assert document["document"]["unifiedEarTag"] == {
            "raw": "9876543210",
            "normalized": "9876543210",
            "status": "readable",
            "alternatives": [],
            "reviewReason": None,
            "sourceRegionId": "manual_mother_individual_id",
            "boundingBox": None,
        }
        assert document["saleCalves"][0]["priceYen"]["raw"] == "12,345円"
        assert document["saleCalves"][0]["priceYen"]["normalized"] == "12345"
        assert document["saleCalves"][1]["priceYen"] | {"confidence": None} == {
            "raw": None,
            "normalized": None,
            "status": "uncertain",
            "confidence": None,
            "alternatives": [],
            "reviewReason": "LOCAL_INK_OCR_BLANK",
            "sourceRegionId": "price-1",
            "boundingBox": {"x": 15, "y": 25, "width": 30, "height": 20},
        }
        assert len(document["saleCalves"]) == 2
        assert document["calvings"][0]["parity"] == {
            "raw": "1",
            "normalized": "1",
            "status": "readable",
            "alternatives": [],
            "reviewReason": None,
            "sourceRegionId": "calving-parity",
            "boundingBox": {"x": 17, "y": 27, "width": 30, "height": 20},
        }
        assert document["customFields"]["localMark"]["raw"] == "Yes"
        assert document["customFields"]["localMark"]["status"] == "readable"
        assert document["customFields"]["manualMark"]["raw"] is None
        assert document["customFields"]["manualMark"]["status"] == "uncertain"
        assert document["customFields"]["manualMark"]["reviewReason"] == (
            "MANUAL_CHOICE_REQUIRED"
        )
        assert document["customFields"]["openAiMark"]["raw"] == "Yes"
        assert document["customFields"]["openAiMark"]["status"] == "readable"
        assert [item["code"] for item in document["reviewItems"]] == [
            "BIRTH_DATE_NUMBERS_INVALID",
            "LOW_REGISTRATION_CONFIDENCE",
            "MASK_COVERAGE_HIGH",
        ]
        assert [item["sourceRegionId"] for item in document["reviewItems"]] == [
            "calving-date",
            None,
            None,
        ]

        cells = mapped_cells["cells"]
        assert [cell["mappingId"] for cell in cells] == EXPECTED_MAPPED_CELL_ORDER
        assert {
            cell["mappingId"] for cell in cells if cell["sentToOpenAi"] is True
        } == EXPECTED_SENT_MAPPING_IDS
        by_id = {cell["mappingId"]: cell for cell in cells}
        for mapping_id, (aligned_sha, handwriting_sha) in EXPECTED_MAPPED_CELL_SHA.items():
            assert by_id[mapping_id]["alignedCellSha256"] == aligned_sha
            assert by_id[mapping_id]["handwritingHintSha256"] == handwriting_sha
        assert by_id["price-2"]["localResolution"] == "trailing_blank_row"
        assert by_id["price-2"]["response"] is None
        assert by_id["calving-parity"]["localResolution"] == "fixed_record_key"
        assert by_id["mark-local"]["localResolution"] == "mark_detector"
        assert by_id["mark-manual"]["localResolution"] == "manual_choice_required"
        assert by_id["price-1"]["response"] == document["saleCalves"][1]["priceYen"]
        assert diagnostics["mappedCellsResponses"] == [
            {
                "mappingId": cell["mappingId"],
                "openAiMappingId": cell["openAiMappingId"],
                "targetPath": cell["targetPath"],
                "response": cell["response"],
            }
            for cell in cells
            if cell["sentToOpenAi"] is True
        ]
        for name, artifact in artifacts.items():
            assert artifact.sha256 == hashlib.sha256(
                storage.read_bytes(artifact.relative_path)
            ).hexdigest(), name
        assert workflow.trace == [
            "PREPARING_MAPPED_CELLS",
            "CALLING_OCR",
            "provider.extract",
            "VALIDATING_RESULT",
            "WRITING_ARTIFACTS",
        ]
    finally:
        database.dispose()
