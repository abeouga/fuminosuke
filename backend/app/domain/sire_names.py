from __future__ import annotations

import re
import unicodedata

from app.domain.template_candidates import canonical_managed_candidate

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
    return canonical_managed_candidate(value, candidates, allowed_suffixes)
