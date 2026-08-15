from app.domain.numeric_values import normalize_document_numbers
from app.domain.ocr_factory import blank_ocr_document, blank_reading
from app.domain.ocr_models import FieldReading, ReadingStatus, SaleCalf


def _reading(
    raw: str,
    *,
    normalized: str | None = None,
) -> FieldReading:
    return FieldReading(
        raw=raw,
        normalized=normalized,
        status=ReadingStatus.READABLE,
        source_region_id="test",
    )


def _sale_calf(notes: FieldReading) -> SaleCalf:
    return SaleCalf(
        parity=blank_reading("test"),
        age_days=blank_reading("test"),
        weight_kg=blank_reading("test"),
        price_yen=blank_reading("test"),
        sire_name=blank_reading("test"),
        sale_date=blank_reading("test"),
        notes=notes,
    )


def test_review_save_accepts_selected_era_and_valid_note_prefix() -> None:
    document = blank_ocr_document(provider="test", source_regions=[])
    document.document.birth_date = _reading(
        "26 9 27",
        normalized="2014年9月27日",
    )
    document.sale_calves.append(_sale_calf(_reading("♂双子")))

    normalized = normalize_document_numbers(document)

    assert normalized.document.birth_date.normalized == "2014年9月27日"
    assert normalized.document.birth_date.status is ReadingStatus.READABLE
    assert normalized.sale_calves[0].notes.normalized == "♂双子"
    assert normalized.sale_calves[0].notes.status is ReadingStatus.READABLE


def test_review_save_infers_era_and_blocks_invalid_note_prefix() -> None:
    document = blank_ocr_document(provider="test", source_regions=[])
    document.document.birth_date = _reading("26 9 27")
    document.sale_calves.append(_sale_calf(_reading("る")))
    document.sale_calves[0].sale_date = _reading("6 11 7")

    normalized = normalize_document_numbers(document)

    assert normalized.document.birth_date.status is ReadingStatus.READABLE
    assert normalized.document.birth_date.normalized == "2014年9月27日"
    assert normalized.sale_calves[0].notes.status is ReadingStatus.UNCERTAIN
    assert normalized.sale_calves[0].notes.review_reason == "SALE_NOTE_SEX_SYMBOL_UNRESOLVED"
    assert normalized.sale_calves[0].sale_date.status is ReadingStatus.READABLE
    assert normalized.sale_calves[0].sale_date.normalized == "2024年11月7日"
