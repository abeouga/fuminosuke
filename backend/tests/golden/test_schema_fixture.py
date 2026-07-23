from pathlib import Path

from app.domain.ocr_models import OcrDocument


def test_mock_result_is_a_valid_golden_schema_fixture() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "mock-result.json"
    )
    result = OcrDocument.model_validate_json(fixture.read_text(encoding="utf-8"))
    assert result.document.unified_ear_tag.normalized == "1234567890"
    assert result.document.registration_number.status.value == "blank"

