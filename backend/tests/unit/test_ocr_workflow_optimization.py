from types import SimpleNamespace

import numpy as np

from app.domain.ocr_factory import blank_ocr_document
from app.domain.ocr_models import ReadingStatus
from app.domain.template_config import FieldMappingConfig, MarkOptionConfig
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.ocr.deterministic_mapper import assign_mapped_reading, build_mapped_document
from app.ocr.transcription_models import TranscriptionBatch, TranscriptionItem
from app.services.ocr_result_service import OcrResultService
from app.services.ocr_workflow_service import OcrWorkflowService


def _mapping(
    mapping_id: str, target_path: str, *, mapping_type: str = "text"
) -> FieldMappingConfig:
    return FieldMappingConfig(
        mapping_id=mapping_id,
        target_path=target_path,
        label=target_path,
        x=0,
        y=0,
        width=20,
        height=10,
        mapping_type=mapping_type,
    )


def _crop(*, ink: bool) -> SimpleNamespace:
    image = np.full((10, 20, 3), 255, dtype=np.uint8)
    if ink:
        image[3:5, 5:7] = 0
    return SimpleNamespace(handwriting_only=image)


def test_fixed_calving_record_keys_exclude_sale_record_keys() -> None:
    structure = {
        "sections": [
            {"id": "sale", "collectionPath": "saleCalves"},
            {"id": "calving", "collectionPath": "calvings"},
        ],
        "groups": [
            {
                "id": "sale-1",
                "sectionId": "sale",
                "order": 1,
                "recordKeyField": "parity",
                "recordKeyValue": "1",
            },
            {
                "id": "calving-11",
                "sectionId": "calving",
                "order": 11,
                "recordKeyField": "parity",
                "recordKeyValue": "11",
            },
        ],
    }

    assert OcrWorkflowService._fixed_calving_record_keys(structure) == {"calvings.10.parity": "11"}


def test_birth_calving_and_sale_dates_request_era_date_transcription() -> None:
    assert OcrWorkflowService._transcription_format("document.birthDate") == (
        "japanese_era_date_numbers"
    )
    assert OcrWorkflowService._transcription_format("calvings.0.calvingDate") == (
        "japanese_era_date_numbers"
    )
    assert OcrWorkflowService._transcription_format("saleCalves.0.saleDate") == (
        "japanese_era_date_numbers"
    )


def test_trailing_blank_gate_keeps_leading_blanks_and_skips_only_after_last_ink() -> None:
    mappings = [
        _mapping("price-0", "saleCalves.0.priceYen"),
        _mapping("price-1", "saleCalves.1.priceYen"),
        _mapping("price-2", "saleCalves.2.priceYen"),
        _mapping("parity-2", "saleCalves.2.parity"),
        _mapping("date-0", "calvings.0.calvingDate"),
        _mapping("date-1", "calvings.1.calvingDate"),
    ]
    extraction = SimpleNamespace(
        config=SimpleNamespace(field_mappings=mappings),
        crops={
            "price-0": _crop(ink=False),
            "price-1": _crop(ink=True),
            "price-2": _crop(ink=False),
            # Printed/fixed parity must not make an otherwise blank row active.
            "parity-2": _crop(ink=True),
            "date-0": _crop(ink=False),
            "date-1": _crop(ink=False),
        },
    )

    skipped = OcrWorkflowService._trailing_blank_rows(extraction, {})

    assert skipped == {("saleCalves", 2)}
    assert ("saleCalves", 0) not in skipped
    # With no positive local evidence, the collection is left untouched.
    assert not any(collection == "calvings" for collection, _ in skipped)


def test_nonblank_local_mark_keeps_its_row_and_only_skips_rows_below_it() -> None:
    mappings = [
        _mapping("mark-0", "calvings.0.outcome", mapping_type="mark"),
        _mapping("mark-1", "calvings.1.outcome", mapping_type="mark"),
        _mapping("mark-2", "calvings.2.outcome", mapping_type="mark"),
    ]
    extraction = SimpleNamespace(
        config=SimpleNamespace(field_mappings=mappings),
        crops={},
    )
    states = {
        "mark-0": (ReadingStatus.BLANK, None, None),
        "mark-1": (ReadingStatus.READABLE, "双", None),
        "mark-2": (ReadingStatus.BLANK, None, None),
    }

    assert OcrWorkflowService._trailing_blank_rows(extraction, states) == {("calvings", 2)}


def test_mark_crop_and_review_preview_keep_the_same_canonical_box() -> None:
    mapping = FieldMappingConfig(
        mapping_id="choice-city",
        target_path="customFields.cityType",
        label="市区町村",
        x=383,
        y=519,
        width=66,
        height=51,
        mapping_type="mark",
        mark_options=[
            MarkOptionConfig(
                id="city",
                label="市",
                value="city",
                x=0,
                y=0,
                width=33,
                height=51,
            ),
            MarkOptionConfig(
                id="ward",
                label="区",
                value="ward",
                x=33,
                y=0,
                width=33,
                height=51,
            ),
        ],
    )
    image = np.full((59, 74, 3), 255, dtype=np.uint8)
    crop = SimpleNamespace(
        bbox=(379, 515, 74, 59),
        aligned_color=image,
        handwriting_only=image.copy(),
    )

    region = OcrWorkflowService._mark_region_input(mapping, crop)
    local_box = OcrWorkflowService._mark_bounding_box(mapping, crop)
    document = build_mapped_document(
        OcrProviderRequest(regions=[region]),
        TranscriptionBatch(
            items=[
                TranscriptionItem(
                    mapping_id=mapping.mapping_id,
                    raw="市",
                    status=ReadingStatus.READABLE,
                )
            ]
        ),
    )
    job = SimpleNamespace(
        id="job-choice",
        config_snapshot_json={
            "fieldMappings": [mapping.model_dump(mode="json", by_alias=True)],
            "recordStructure": {"fieldBindings": []},
        },
    )

    previews = OcrResultService._field_previews(
        job,
        document.model_dump(mode="json", by_alias=True),
    )

    assert region.bounding_box == crop.bbox
    assert local_box.model_dump() == {
        "x": 379,
        "y": 515,
        "width": 74,
        "height": 59,
    }
    assert previews[0]["mappingId"] == mapping.mapping_id
    assert previews[0]["boundingBox"] == {
        "x": 379,
        "y": 515,
        "width": 74,
        "height": 59,
    }


def test_local_ink_turns_openai_blank_into_uncertain_review() -> None:
    document = blank_ocr_document(provider="openai", source_regions=["cell-1", "cell-2"])
    assign_mapped_reading(
        document,
        "customFields.inked",
        document.document.cattle_name.model_copy(update={"source_region_id": "cell-1"}),
    )
    assign_mapped_reading(
        document,
        "customFields.empty",
        document.document.cattle_name.model_copy(update={"source_region_id": "cell-2"}),
    )
    extraction = SimpleNamespace(
        crops={"cell-1": _crop(ink=True), "cell-2": _crop(ink=False)}
    )
    regions = [
        OcrRegionInput(
            region_id="cell-1",
            image_png=b"crop",
            target_path="customFields.inked",
        ),
        OcrRegionInput(
            region_id="cell-2",
            image_png=b"crop",
            target_path="customFields.empty",
        ),
    ]

    OcrWorkflowService._flag_local_ink_conflicts(document, regions, extraction)

    assert document.custom_fields["inked"].status is ReadingStatus.UNCERTAIN
    assert document.custom_fields["inked"].review_reason == "LOCAL_INK_OCR_BLANK"
    assert document.custom_fields["empty"].status is ReadingStatus.BLANK
