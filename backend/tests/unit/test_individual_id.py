from app.domain.individual_id import individual_id_from_reading
from app.domain.ocr_models import FieldReading, ReadingStatus


def test_normalized_individual_id_takes_precedence_over_raw_ocr_text() -> None:
    reading = FieldReading(
        raw="3554-8052-3",
        normalized="1355480523",
        status=ReadingStatus.READABLE,
        source_region_id="ear_tag",
    )

    assert individual_id_from_reading(reading) == "1355480523"
