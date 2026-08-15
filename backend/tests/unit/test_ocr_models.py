import asyncio
from pathlib import Path

from app.domain.numeric_values import normalize_document_numbers, normalize_yen
from app.domain.ocr_factory import blank_ocr_document, blank_reading
from app.domain.ocr_models import FieldReading, OcrDocument, ReadingStatus, SaleCalf
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.ocr.mock_provider import MockOcrProvider
from app.validation.business_validators import DeterministicValidator


def test_blank_and_uncertain_are_distinct() -> None:
    blank = blank_reading("header_identity")
    assert blank.status is ReadingStatus.BLANK
    normalized_blank = FieldReading(
        raw="候補",
        normalized="候補",
        status=ReadingStatus.BLANK,
        source_region_id="header_identity",
    )
    assert normalized_blank.raw is None
    assert normalized_blank.normalized is None
    uncertain = FieldReading(
        raw="候補",
        normalized=None,
        status=ReadingStatus.UNCERTAIN,
        alternatives=["侯補"],
        review_reason="LOW_CONFIDENCE",
        source_region_id="header_identity",
    )
    assert uncertain.raw == "候補"
    assert uncertain.normalized is None


def test_readable_without_raw_is_normalized_to_uncertain() -> None:
    reading = FieldReading(
        raw="",
        normalized="推測値",
        status=ReadingStatus.READABLE,
        source_region_id="header_identity",
    )
    assert reading.status is ReadingStatus.UNCERTAIN
    assert reading.raw is None
    assert reading.normalized is None


def test_mock_provider_needs_no_api_key() -> None:
    response = asyncio.run(
        MockOcrProvider().extract(
            OcrProviderRequest(
                regions=[OcrRegionInput(region_id="header_identity", image_png=b"png")]
            )
        )
    )
    assert response.provider == "mock"
    assert response.document.processing_metadata.source_regions == ["header_identity"]


def test_deterministic_validator_flags_bad_ear_tag() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    document = OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))
    document.document.unified_ear_tag.raw = "123"
    report = DeterministicValidator().validate(document)
    issue = next(
        item for item in report.issues if item.code == "INDIVIDUAL_ID_REQUIRED"
    )
    assert issue.severity == "error"


def test_deterministic_validator_accepts_japanese_middle_dot_date() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    document = OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))
    document.document.birth_date = FieldReading(
        raw="28・7・25",
        normalized=None,
        status=ReadingStatus.READABLE,
        source_region_id="calvings_left",
    )
    report = DeterministicValidator().validate(document)
    assert not any(issue.path == "document.birthDate" for issue in report.issues)


def test_validator_ignores_unmapped_fixed_schema_fields() -> None:
    document = blank_ocr_document(
        provider="openai-mapped",
        source_regions=["price_1", "sire_1"],
    )

    report = DeterministicValidator().validate(
        document,
        configured_paths={
            "saleCalves.0.priceYen",
            "saleCalves.0.sireName",
        },
    )

    assert not any(issue.code == "INDIVIDUAL_ID_REQUIRED" for issue in report.issues)


def test_price_grouping_is_canonicalized_and_ambiguous_decimals_are_blocked() -> None:
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
            price_yen=FieldReading(
                raw="762.300",
                normalized="762.300",
                status=ReadingStatus.READABLE,
                source_region_id="price",
            ),
            sire_name=blank_reading("unmapped"),
            sale_date=blank_reading("unmapped"),
            notes=blank_reading("unmapped"),
        )
    )

    normalized = normalize_document_numbers(document)

    assert normalize_yen("762.300") == "762300"
    assert normalize_yen("826-100") == "826100"
    assert normalized.sale_calves[0].price_yen.normalized == "762300"
    assert not any(
        issue.path == "saleCalves.0.priceYen"
        for issue in DeterministicValidator().validate(normalized).issues
    )

    normalized.sale_calves[0].price_yen.normalized = None
    normalized.sale_calves[0].price_yen.raw = "762.30"
    report = DeterministicValidator().validate(normalized)
    issue = next(
        item
        for item in report.issues
        if item.path == "saleCalves.0.priceYen" and item.code == "NUMERIC_SYNTAX"
    )
    assert issue.severity == "error"


def test_sale_calf_parity_and_sire_name_must_be_meaningful_before_confirmation() -> None:
    document = blank_ocr_document(
        provider="openai-mapped",
        source_regions=["parity_1", "sire_1"],
    )
    document.sale_calves.append(
        SaleCalf(
            parity=FieldReading(
                raw="/",
                normalized=None,
                status=ReadingStatus.READABLE,
                source_region_id="parity_1",
            ),
            age_days=blank_reading("unmapped"),
            weight_kg=blank_reading("unmapped"),
            price_yen=blank_reading("unmapped"),
            sire_name=FieldReading(
                raw="、",
                normalized="、",
                status=ReadingStatus.READABLE,
                source_region_id="sire_1",
            ),
            sale_date=blank_reading("unmapped"),
            notes=blank_reading("unmapped"),
        )
    )

    report = DeterministicValidator().validate(
        document,
        configured_paths={
            "saleCalves.0.parity",
            "saleCalves.0.sireName",
        },
    )

    assert report.valid is False
    assert any(
        issue.path == "saleCalves.0.parity"
        and issue.code == "NUMERIC_SYNTAX"
        and issue.severity == "error"
        for issue in report.issues
    )
    assert any(
        issue.path == "saleCalves.0.sireName"
        and issue.code == "TEXT_VALUE_INVALID"
        and issue.severity == "error"
        for issue in report.issues
    )
