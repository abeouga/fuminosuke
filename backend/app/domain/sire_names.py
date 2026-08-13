from __future__ import annotations

import re
import unicodedata

MAX_SIRE_NAME_LENGTH = 100
MAX_SIRE_NAME_POOL_SIZE = 500
SIRE_NAME_ALLOWED_SUFFIXES = ("ET",)


def clean_sire_name(value: str) -> str:
    cleaned = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).strip())
    if not cleaned:
        raise ValueError("Sire name is required")
    if len(cleaned) > MAX_SIRE_NAME_LENGTH:
        raise ValueError("Sire name is too long")
    return cleaned


def normalize_sire_name(value: str) -> str:
    return clean_sire_name(value).casefold()


def canonical_sire_candidate(
    value: str | None,
    candidates: tuple[str, ...],
    allowed_suffixes: tuple[str, ...] = SIRE_NAME_ALLOWED_SUFFIXES,
) -> str | None:
    if value is None:
        return None
    try:
        normalized_value = normalize_sire_name(value)
    except ValueError:
        return None
    for candidate in candidates:
        normalized_candidate = normalize_sire_name(candidate)
        if normalized_value == normalized_candidate:
            return candidate
        for suffix in allowed_suffixes:
            if normalized_value == normalize_sire_name(f"{candidate}{suffix}"):
                return f"{candidate}{suffix}"
    return None
