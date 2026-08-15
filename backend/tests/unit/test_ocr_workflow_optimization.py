from types import SimpleNamespace

import numpy as np

from app.domain.ocr_models import ReadingStatus
from app.domain.template_config import FieldMappingConfig
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
