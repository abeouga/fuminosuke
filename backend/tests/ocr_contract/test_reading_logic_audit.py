from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import JobArtifact, OcrResult
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.ocr_models import ReadingStatus
from app.ocr.base import OcrProviderRequest, OcrProviderResponse, OcrRegionInput
from app.ocr.deterministic_mapper import build_mapped_document, get_mapped_reading
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem
from app.repositories.job_repository import JobRepository
from app.services.ocr_request_preparation_service import OcrRequestPreparer
from app.services.ocr_response_finalization_service import OcrResponseFinalizer
from app.services.ocr_workflow_service import OcrWorkflowService
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator
from tests.ocr_contract.test_phase5a_request_preparation_contract import (
    _crop,
    _extraction,
    _mapping,
    _snapshot,
)


@pytest.mark.parametrize(
    ("raw", "expected", "status"),
    [
        ("非該当", "非該当", ReadingStatus.READABLE),
        ("非該当・保留", "非該当・保留", ReadingStatus.READABLE),
        ("未該当", None, ReadingStatus.UNCERTAIN),
        ("該当・未知", None, ReadingStatus.UNCERTAIN),
        ("該当", "該当", ReadingStatus.READABLE),
        ("保留、該当", "該当・保留", ReadingStatus.READABLE),
        (None, None, ReadingStatus.BLANK),
    ],
)
def test_r1_mark_labels_require_complete_candidate_matches(
    raw: str | None, expected: str | None, status: ReadingStatus
) -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="physical-choice",
                image_png=b"unused-offline",
                target_path="customFields.choice",
                source_region_id="physical-choice",
                bounding_box=(10, 20, 30, 40),
                candidates=("該当", "非該当", "保留"),
                candidate_type="mark_choice",
            )
        ]
    )
    document = build_mapped_document(
        request,
        TranscriptionBatch(
            items=[
                TranscriptionItem(
                    mapping_id="physical-choice",
                    raw=raw,
                    status=ReadingStatus.READABLE if raw else ReadingStatus.BLANK,
                )
            ]
        ),
    )
    reading = document.custom_fields["choice"]
    assert reading.normalized == expected
    assert reading.status is status
    assert reading.source_region_id == "physical-choice"
    assert reading.bounding_box is not None
    assert reading.bounding_box.model_dump() == {"x": 10, "y": 20, "width": 30, "height": 40}
    if status is ReadingStatus.UNCERTAIN:
        assert reading.raw == raw
        assert reading.review_reason == "MARK_CHOICE_UNRESOLVED"


@pytest.mark.parametrize(
    ("path", "raw", "normalized"),
    [
        ("document.birthDate", "2020 1 2", "2020年1月2日"),
        ("saleCalves.0.saleDate", "2020 1 2", "2020年1月2日"),
        ("calvings.0.calvingDate", "2020 1 2", "2020年1月2日"),
        ("saleCalves.0.notes", "♂出荷", "♂出荷"),
    ],
)
@pytest.mark.parametrize("status", [ReadingStatus.UNCERTAIN, ReadingStatus.READABLE])
def test_r2_format_normalization_does_not_promote_uncertain_readings(
    path: str, raw: str, normalized: str, status: ReadingStatus
) -> None:
    extraction = _extraction("r2-offline")
    mappings = [
        _mapping("subject", path, "Same label", 1),
        _mapping("untouched", "customFields.untouched", "Same label", 2),
    ]
    extraction.config = extraction.config.model_copy(update={"field_mappings": mappings})
    extraction.crops = {
        mapping.mapping_id: _crop(mapping, index, ink=True)
        for index, mapping in enumerate(mappings)
    }
    extraction.marks = []
    prepared = OcrRequestPreparer().prepare(
        extraction=extraction, job_snapshot={}, provider_name="openai", should_cancel=None
    )
    document = build_mapped_document(
        prepared.request,
        TranscriptionBatch(
            items=[
                TranscriptionItem(
                    mapping_id="subject",
                    raw=raw,
                    status=status,
                    alternatives=["別候補"] if status is ReadingStatus.UNCERTAIN else [],
                    review_reason="MODEL_UNCERTAIN" if status is ReadingStatus.UNCERTAIN else None,
                ),
                TranscriptionItem(mapping_id="untouched", raw="001 A", status="readable"),
            ]
        ),
    )
    before = get_mapped_reading(document, path)
    assert before is not None
    before = before.model_copy(deep=True)
    untouched = document.custom_fields["untouched"].model_copy(deep=True)
    finalized = OcrResponseFinalizer(DeterministicValidator()).finalize(
        prepared=prepared,
        provider_response=OcrProviderResponse(
            document=document,
            provider="openai",
            model_id="offline",
            prompt_version=None,
            api_call_count=0,
        ),
        extraction=extraction,
        job_snapshot={},
        provider_name="openai",
    )
    reading = get_mapped_reading(finalized.response.document, path)
    assert reading is not None
    assert reading.status is status
    assert reading.source_region_id == before.source_region_id
    assert reading.bounding_box == before.bounding_box
    assert reading.alternatives == before.alternatives
    assert reading.review_reason == before.review_reason
    if status is ReadingStatus.UNCERTAIN:
        assert reading == before
    else:
        assert reading.normalized == normalized
    assert finalized.response.document.custom_fields["untouched"] == untouched
    assert finalized.mapped_cells_payload is not None
    cells = finalized.mapped_cells_payload["cells"]
    assert isinstance(cells, list)
    subject = next(cell for cell in cells if cell["mappingId"] == "subject")
    assert subject["response"] == reading.model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize("selection_mode", ["single", "multiple"])
@pytest.mark.parametrize(
    ("raw", "provider_status"),
    [
        ("Yes", ReadingStatus.READABLE),
        ("Yes・No", ReadingStatus.READABLE),
        ("Yes", ReadingStatus.UNCERTAIN),
    ],
)
def test_r3_provider_mark_obeys_single_selection_without_changing_local_or_manual(
    selection_mode: Literal["single", "multiple"],
    raw: str,
    provider_status: ReadingStatus,
) -> None:
    extraction = _extraction("r3-offline")
    mapping = next(
        item for item in extraction.config.field_mappings if item.mapping_id == "mark-openai"
    )
    mapping.mark_selection_mode = selection_mode
    snapshot = _snapshot(send_full_document=False)
    prepared = OcrRequestPreparer().prepare(
        extraction=extraction, job_snapshot=snapshot, provider_name="openai", should_cancel=None
    )
    document = build_mapped_document(
        prepared.request,
        TranscriptionBatch(
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id,
                    raw=raw if region.region_id == "mark-openai" else None,
                    status=provider_status if region.region_id == "mark-openai" else "blank",
                )
                for region in prepared.request.regions
            ]
        ),
    )
    before = document.custom_fields["openAiMark"].model_copy(deep=True)
    finalized = OcrResponseFinalizer(DeterministicValidator()).finalize(
        prepared=prepared,
        provider_response=OcrProviderResponse(
            document=document,
            provider="openai",
            model_id="offline",
            prompt_version=None,
            api_call_count=0,
        ),
        extraction=extraction,
        job_snapshot=snapshot,
        provider_name="openai",
    )
    fields = finalized.response.document.custom_fields
    reading = fields["openAiMark"]
    invalid_selection = selection_mode == "single" and raw == "Yes・No"
    expected_status = ReadingStatus.UNCERTAIN if invalid_selection else provider_status
    assert reading.status is expected_status
    assert reading.raw == before.raw
    assert reading.source_region_id == before.source_region_id
    assert reading.bounding_box == before.bounding_box
    assert reading.alternatives == before.alternatives
    if expected_status is ReadingStatus.UNCERTAIN:
        assert reading.normalized is None
        assert reading.review_reason == "MARK_CHOICE_UNRESOLVED"
    else:
        assert reading.normalized == before.normalized
    assert fields["localMark"].status is ReadingStatus.READABLE
    assert fields["localMark"].raw == "Yes"
    assert fields["manualMark"].status is ReadingStatus.UNCERTAIN
    assert fields["manualMark"].review_reason == "MANUAL_CHOICE_REQUIRED"
    assert finalized.mapped_cells_payload is not None
    cells = finalized.mapped_cells_payload["cells"]
    assert isinstance(cells, list)
    cell = next(cell for cell in cells if cell["mappingId"] == "mark-openai")
    assert cell["response"] == reading.model_dump(mode="json", by_alias=True)


def test_sparse_rows_keep_long_text_blank_and_uncertain_value_at_their_physical_cells() -> None:
    extraction = _extraction("sparse-offline")
    mappings = [
        _mapping(f"{field}-{row}", f"saleCalves.{row}.{field}", "Same label", row * 2 + col)
        for row in range(4)
        for col, field in enumerate(("sireName", "priceYen"))
    ]
    extraction.config = extraction.config.model_copy(update={"field_mappings": mappings})
    extraction.crops = {
        mapping.mapping_id: _crop(mapping, index, ink=index // 2 in {0, 2})
        for index, mapping in enumerate(mappings)
    }
    extraction.marks = []
    prepared = OcrRequestPreparer().prepare(
        extraction=extraction, job_snapshot={}, provider_name="openai", should_cancel=None
    )
    assert [region.region_id for region in prepared.request.regions] == [
        mapping.mapping_id for mapping in mappings[:6]
    ]
    values = {
        "sireName-0": "長い名称" * 100,
        "priceYen-0": "001",
        "sireName-2": "末尾候補",
        "priceYen-2": "12,345円",
    }
    document = build_mapped_document(
        prepared.request,
        TranscriptionBatch(
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id,
                    raw=values.get(region.region_id),
                    status=(
                        "uncertain"
                        if region.region_id == "sireName-2"
                        else "readable"
                        if region.region_id in values
                        else "blank"
                    ),
                    alternatives=["別候補"] if region.region_id == "sireName-2" else [],
                    review_reason="MODEL_UNCERTAIN" if region.region_id == "sireName-2" else None,
                )
                for region in reversed(prepared.request.regions)
            ]
        ),
    )
    finalized = OcrResponseFinalizer(DeterministicValidator()).finalize(
        prepared=prepared,
        provider_response=OcrProviderResponse(
            document=document,
            provider="openai",
            model_id="offline",
            prompt_version=None,
            api_call_count=0,
        ),
        extraction=extraction,
        job_snapshot={},
        provider_name="openai",
    )
    rows = finalized.response.document.sale_calves
    assert len(rows) == 3
    assert rows[0].sire_name.raw == values["sireName-0"]
    assert rows[0].price_yen.normalized == "001"
    assert rows[1].sire_name.status is ReadingStatus.BLANK
    assert rows[1].price_yen.status is ReadingStatus.BLANK
    assert rows[2].sire_name.raw == "末尾候補"
    assert rows[2].sire_name.status is ReadingStatus.UNCERTAIN
    assert rows[2].sire_name.review_reason == "MODEL_UNCERTAIN"
    assert rows[2].sire_name.alternatives == ["別候補"]
    assert rows[2].price_yen.normalized == "12345"
    assert finalized.mapped_cells_payload is not None
    cells = finalized.mapped_cells_payload["cells"]
    assert isinstance(cells, list)
    assert len(cells) == 8
    for cell, mapping in zip(cells, mappings, strict=True):
        assert cell["mappingId"] == mapping.mapping_id
        assert cell["targetPath"] == mapping.target_path
        response = cell["response"]
        if mapping in mappings[6:]:
            assert response is None
        else:
            assert response["sourceRegionId"] == mapping.mapping_id
            assert response["boundingBox"] == cell["boundingBox"]


class _AuditProvider:
    async def extract(self, request: OcrProviderRequest) -> OcrProviderResponse:
        values = {
            "mark-openai": ("非該当", ReadingStatus.READABLE),
            "single-conflict": ("Yes・No", ReadingStatus.READABLE),
            "calving-date": ("2020 1 2", ReadingStatus.UNCERTAIN),
        }
        batch = TranscriptionBatch(
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id,
                    raw=values.get(region.region_id, (None, ReadingStatus.BLANK))[0],
                    status=values.get(region.region_id, (None, ReadingStatus.BLANK))[1],
                    review_reason="MODEL_UNCERTAIN" if region.region_id == "calving-date" else None,
                )
                for region in reversed(request.regions)
            ]
        )
        return OcrProviderResponse(
            document=build_mapped_document(request, batch),
            provider="openai",
            model_id="offline-audit",
            prompt_version=None,
            api_call_count=0,
        )


def test_fixed_readings_survive_workflow_db_and_artifact_persistence(
    settings_factory: Callable[..., Settings], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_factory(data_dir=tmp_path, ocr_provider="openai")
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    snapshot = _snapshot(send_full_document=False)
    try:
        with database.session() as session:
            job = JobRepository(session).create(
                job_type=JobType.EXTRACT_DOCUMENT,
                request_hash="a" * 64,
                config_snapshot=snapshot,
                status=JobStatus.PROCESSING,
            )
        extraction = _extraction(job.id)
        remote = next(
            mapping
            for mapping in extraction.config.field_mappings
            if mapping.mapping_id == "mark-openai"
        )
        remote.mark_options[0].label = "該当"
        remote.mark_options[1].label = "非該当"
        conflict = _mapping(
            "single-conflict",
            "customFields.singleConflict",
            "Same label",
            12,
            mapping_type="mark",
        )
        extraction.config.field_mappings.append(conflict)
        extraction.crops[conflict.mapping_id] = _crop(conflict, 12, ink=True)
        workflow = OcrWorkflowService(settings=settings, database=database, storage=storage)
        monkeypatch.setattr(workflow, "_create_provider", lambda _name: _AuditProvider())
        workflow.process(extraction)
        with database.session() as session:
            result = session.scalar(select(OcrResult).where(OcrResult.job_id == job.id))
            assert result is not None
            assert result.revision == 1
            assert result.api_call_count == 0
            assert result.raw_candidate_json == result.current_corrected_json
            saved = result.raw_candidate_json
            assert saved["customFields"]["openAiMark"]["normalized"] == "非該当"
            assert saved["customFields"]["openAiMark"]["status"] == "readable"
            invalid = saved["customFields"]["singleConflict"]
            assert invalid["raw"] == "Yes・No"
            assert invalid["normalized"] is None
            assert invalid["status"] == "uncertain"
            assert invalid["reviewReason"] == "MARK_CHOICE_UNRESOLVED"
            date = saved["calvings"][0]["calvingDate"]
            assert date["status"] == "uncertain"
            assert date["reviewReason"] == "MODEL_UNCERTAIN"
            artifacts = {
                artifact.artifact_name: json.loads(storage.read_bytes(artifact.relative_path))
                for artifact in session.scalars(
                    select(JobArtifact).where(JobArtifact.job_id == job.id)
                )
            }
            assert artifacts["result"]["result"] == saved
            assert artifacts["result"]["revision"] == 1
            for cell in artifacts["mappedCells"]["cells"]:
                if cell["mappingId"] == "single-conflict":
                    assert cell["response"] == invalid
                    assert invalid["sourceRegionId"] == cell["mappingId"]
                    assert invalid["boundingBox"] == cell["boundingBox"]
    finally:
        database.dispose()


@pytest.mark.parametrize("collection", ["saleCalves", "calvings"])
@pytest.mark.parametrize("record_key_value", ["", "3"])
def test_r4_explicit_handwritten_parity_keeps_its_row_but_fixed_parity_does_not(
    collection: str, record_key_value: str
) -> None:
    extraction = _extraction("r4-offline")
    value_field = "priceYen" if collection == "saleCalves" else "calvingDate"
    mappings = [
        _mapping(f"value-{row}", f"{collection}.{row}.{value_field}", "Same label", row)
        for row in range(4)
    ] + [_mapping("parity-2", f"{collection}.2.parity", "Parity", 5)]
    extraction.config = extraction.config.model_copy(update={"field_mappings": mappings})
    extraction.crops = {
        mapping.mapping_id: _crop(mapping, index, ink=mapping.mapping_id in {"value-0", "parity-2"})
        for index, mapping in enumerate(mappings)
    }
    extraction.marks = []
    snapshot = {
        "recordStructure": {
            "verified": True,
            "sections": [{"id": "records", "collectionPath": collection}],
            "groups": [
                {
                    "id": f"row-{row}",
                    "sectionId": "records",
                    "order": row + 1,
                    "recordKeyField": "parity",
                    "recordKeyValue": record_key_value if row == 2 else str(row + 1),
                }
                for row in range(4)
            ],
        }
    }
    prepared = OcrRequestPreparer().prepare(
        extraction=extraction, job_snapshot=snapshot, provider_name="openai", should_cancel=None
    )
    expected_ids = (
        ["value-0", "value-1", "value-2", "parity-2"] if not record_key_value else ["value-0"]
    )
    assert [region.region_id for region in prepared.request.regions] == expected_ids
    assert prepared.mapped_cells_payload is not None
    assert prepared.mapped_cells_payload["trailingBlankRows"] == [
        {"collection": collection, "rowIndex": row}
        for row in ([3] if not record_key_value else [1, 2, 3])
    ]
    cells = prepared.mapped_cells_payload["cells"]
    assert isinstance(cells, list)
    parity_cell = next(cell for cell in cells if cell["mappingId"] == "parity-2")
    assert parity_cell["targetPath"] == f"{collection}.2.parity"
    assert parity_cell["openAiMappingId"] == "parity-2"
    assert parity_cell["sentToOpenAi"] is (not bool(record_key_value))
