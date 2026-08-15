from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from app.domain.japanese_dates import canonical_japanese_birth_date
from app.domain.ocr_models import FieldReading, OcrDocument, ReadingStatus
from app.domain.sale_notes import canonical_sale_note

PLAIN_INTEGER = re.compile(r"^\d+$")
COMMA_GROUPED_INTEGER = re.compile(r"^\d{1,3}(?:,\d{3})+$")
DOT_GROUPED_INTEGER = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
HYPHEN_GROUPED_INTEGER = re.compile(r"^\d{1,3}[-‐‑‒–—−]\d{3}$")
PLAIN_DECIMAL = re.compile(r"^\d+(?:\.\d+)?$")
COMMA_GROUPED_DECIMAL = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def normalize_integer(value: str | None, *, grouped: bool = False) -> str | None:
    compact = _compact(value)
    if compact is None:
        return None
    if PLAIN_INTEGER.fullmatch(compact):
        return compact
    if grouped and (
        COMMA_GROUPED_INTEGER.fullmatch(compact) or DOT_GROUPED_INTEGER.fullmatch(compact)
    ):
        return compact.replace(",", "").replace(".", "")
    return None


def normalize_yen(value: str | None) -> str | None:
    compact = _compact(value)
    if compact is None:
        return None
    compact = compact.removesuffix("円").strip()
    if HYPHEN_GROUPED_INTEGER.fullmatch(compact):
        return re.sub(r"[-‐‑‒–—−]", "", compact)
    return normalize_integer(compact, grouped=True)


def normalize_decimal(value: str | None) -> str | None:
    compact = _compact(value)
    if compact is None:
        return None
    if PLAIN_DECIMAL.fullmatch(compact):
        return compact
    if COMMA_GROUPED_DECIMAL.fullmatch(compact):
        return compact.replace(",", "")
    return None


def decimal_value(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def normalize_document_numbers(document: OcrDocument) -> OcrDocument:
    updated = document.model_copy(deep=True)
    updated.document.birth_date = _normalize_birth_date(updated.document.birth_date)
    for calf in updated.sale_calves:
        calf.parity = _normalize_reading(calf.parity, normalize_integer)
        calf.age_days = _normalize_reading(calf.age_days, normalize_integer)
        calf.weight_kg = _normalize_reading(calf.weight_kg, normalize_decimal)
        calf.price_yen = _normalize_reading(calf.price_yen, normalize_yen)
        calf.sale_date = _normalize_birth_date(calf.sale_date)
        calf.notes = _normalize_sale_note(calf.notes)
    for calving in updated.calvings:
        calving.parity = _normalize_reading(calving.parity, normalize_integer)
        calving.calving_date = _normalize_birth_date(calving.calving_date)
    return updated


def _normalize_birth_date(reading: FieldReading) -> FieldReading:
    if reading.status is ReadingStatus.BLANK:
        return reading
    raw, normalized, reason = canonical_japanese_birth_date(
        reading.raw,
        reading.normalized,
    )
    if reason:
        return reading.model_copy(
            update={
                "raw": raw,
                "normalized": None,
                "status": ReadingStatus.UNCERTAIN,
                "review_reason": reason,
            }
        )
    return reading.model_copy(
        update={
            "raw": raw,
            "normalized": normalized,
            "status": ReadingStatus.READABLE,
            "review_reason": None,
        }
    )


def _normalize_sale_note(reading: FieldReading) -> FieldReading:
    if reading.status is ReadingStatus.BLANK:
        return reading
    normalized = canonical_sale_note(reading.normalized or reading.raw, None)
    if normalized is None:
        return reading.model_copy(
            update={
                "normalized": None,
                "status": ReadingStatus.UNCERTAIN,
                "review_reason": "SALE_NOTE_SEX_SYMBOL_UNRESOLVED",
            }
        )
    return reading.model_copy(
        update={
            "normalized": normalized,
            "status": ReadingStatus.READABLE,
            "review_reason": None,
        }
    )


def _normalize_reading(
    reading: FieldReading,
    normalizer: Callable[[str | None], str | None],
) -> FieldReading:
    if reading.status is not ReadingStatus.READABLE:
        return reading
    candidate = reading.normalized or reading.raw
    normalized = normalizer(candidate)
    return reading.model_copy(update={"normalized": normalized})


def _compact(value: str | None) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    return compact or None
