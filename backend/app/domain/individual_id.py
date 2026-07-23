from __future__ import annotations

import re

from app.domain.ocr_models import FieldReading, OcrDocument, ReadingStatus

INDIVIDUAL_ID_PATTERN = re.compile(r"^\d{10}$")
SEPARATOR_PATTERN = re.compile(r"[\s\-\u2010\u2011\u2012\u2013\u2014\u2015\u30fc\uff0d]")


def normalize_individual_id(value: str) -> str:
    """Return the canonical 10-digit cattle individual identification number."""
    compact = SEPARATOR_PATTERN.sub("", value.strip())
    if not INDIVIDUAL_ID_PATTERN.fullmatch(compact):
        raise ValueError("individual identification number must contain exactly 10 digits")
    return compact


def individual_id_from_reading(reading: FieldReading) -> str:
    if reading.status is not ReadingStatus.READABLE:
        raise ValueError("individual identification number must be readable")
    # The normalized value is the user-confirmed canonical identifier. OCR raw
    # text remains available for auditability.
    candidate = reading.normalized or reading.raw
    if not candidate:
        raise ValueError("individual identification number is required")
    return normalize_individual_id(candidate)


def individual_id_from_document(document: OcrDocument) -> str:
    return individual_id_from_reading(document.document.unified_ear_tag)
