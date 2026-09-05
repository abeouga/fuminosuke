from pathlib import Path

from app.domain.ocr_factory import blank_reading
from app.domain.ocr_models import Calving, OcrDocument, SaleCalf
from app.services.ocr_response_finalization_service import OcrResponseFinalizer


def test_fixed_record_key_is_written_to_the_same_sale_calf_row() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    document = OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))
    document.sale_calves.append(
        SaleCalf(
            parity=blank_reading("unmapped"),
            age_days=blank_reading("unmapped"),
            weight_kg=blank_reading("unmapped"),
            price_yen=blank_reading("price_1"),
            sire_name=blank_reading("sire_1"),
            sale_date=blank_reading("unmapped"),
            notes=blank_reading("unmapped"),
        )
    )
    structure = {
        "sections": [
            {
                "id": "sale",
                "collectionPath": "saleCalves",
            }
        ],
        "groups": [
            {
                "id": "row_1",
                "sectionId": "sale",
                "order": 1,
                "recordKeyField": "parity",
                "recordKeyValue": "3",
                "x": 10,
                "y": 20,
                "width": 200,
                "height": 40,
            }
        ],
    }

    added = OcrResponseFinalizer._apply_record_keys(
        document,
        structure,
        {"saleCalves.0.priceYen", "saleCalves.0.sireName"},
    )

    assert added == {"saleCalves.0.parity"}
    assert document.sale_calves[0].parity.raw == "3"
    assert document.sale_calves[0].parity.normalized == "3"
    assert document.sale_calves[0].parity.source_region_id == "record:row_1"


def test_fixed_record_key_corrects_ocr_parity_without_losing_crop_source() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    document = OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))
    document.sale_calves.append(
        SaleCalf(
            parity=blank_reading("parity_2").model_copy(
                update={
                    "raw": "1",
                    "normalized": "1",
                    "status": "readable",
                }
            ),
            age_days=blank_reading("unmapped"),
            weight_kg=blank_reading("unmapped"),
            price_yen=blank_reading("price_2"),
            sire_name=blank_reading("sire_2"),
            sale_date=blank_reading("unmapped"),
            notes=blank_reading("unmapped"),
        )
    )
    structure = {
        "sections": [{"id": "sale", "collectionPath": "saleCalves"}],
        "groups": [
            {
                "id": "row_2",
                "sectionId": "sale",
                "order": 1,
                "recordKeyField": "parity",
                "recordKeyValue": "2",
            }
        ],
    }

    added = OcrResponseFinalizer._apply_record_keys(
        document,
        structure,
        {"saleCalves.0.parity", "saleCalves.0.priceYen"},
    )

    assert added == {"saleCalves.0.parity"}
    assert document.sale_calves[0].parity.raw == "2"
    assert document.sale_calves[0].parity.normalized == "2"
    assert document.sale_calves[0].parity.source_region_id == "parity_2"


def test_fixed_record_key_does_not_turn_blank_mapped_row_into_a_record() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    document = OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))
    document.sale_calves.append(
        SaleCalf(
            parity=blank_reading("parity_1"),
            age_days=blank_reading("unmapped"),
            weight_kg=blank_reading("unmapped"),
            price_yen=blank_reading("price_1"),
            sire_name=blank_reading("sire_1"),
            sale_date=blank_reading("unmapped"),
            notes=blank_reading("unmapped"),
        )
    )
    structure = {
        "sections": [{"id": "sale", "collectionPath": "saleCalves"}],
        "groups": [
            {
                "id": "row_1",
                "sectionId": "sale",
                "order": 1,
                "recordKeyField": "parity",
                "recordKeyValue": "1",
            }
        ],
    }

    added = OcrResponseFinalizer._apply_record_keys(
        document,
        structure,
        {"saleCalves.0.parity", "saleCalves.0.priceYen"},
    )

    assert added == set()
    assert document.sale_calves[0].parity.raw is None
    assert document.sale_calves[0].parity.source_region_id == "parity_1"


def test_fixed_calving_parity_is_materialized_when_ocr_returns_blank() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    document = OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))
    document.calvings = [
        Calving(
            parity=blank_reading("calving_parity_11"),
            calving_date=blank_reading("calving_date_11"),
            sex=blank_reading("calving_sex_11"),
            outcome=blank_reading("calving_outcome_11"),
            notes=blank_reading("calving_notes_11"),
        )
    ]
    structure = {
        "sections": [{"id": "calvings", "collectionPath": "calvings"}],
        "groups": [
            {
                "id": "calving_row_11",
                "sectionId": "calvings",
                "order": 1,
                "recordKeyField": "parity",
                "recordKeyValue": "11",
            }
        ],
    }

    added = OcrResponseFinalizer._apply_record_keys(
        document,
        structure,
        {"calvings.0.parity", "calvings.0.calvingDate"},
    )

    assert added == {"calvings.0.parity"}
    assert document.calvings[0].parity.raw == "11"
    assert document.calvings[0].parity.normalized == "11"
    assert document.calvings[0].parity.source_region_id == "calving_parity_11"
