import asyncio
from pathlib import Path

from app.domain.ocr_factory import blank_reading
from app.domain.ocr_models import FieldReading, OcrDocument, ReadingStatus
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
