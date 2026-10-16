from dataclasses import dataclass

from app.domain.ocr_models import FieldReading, ReviewItem


@dataclass(frozen=True, slots=True)
class CorrectionValues:
    base_revision: int
    fields: dict[str, FieldReading]
    review_items: list[ReviewItem]
