from __future__ import annotations

import re
import unicodedata

MAX_TEMPLATE_CANDIDATE_SET_COUNT = 30
MAX_TEMPLATE_CANDIDATE_VALUE_COUNT = 500
MAX_TEMPLATE_CANDIDATE_BINDING_COUNT = 300
MAX_TEMPLATE_CANDIDATE_NAME_LENGTH = 100
MAX_TEMPLATE_CANDIDATE_VALUE_LENGTH = 300


def clean_candidate_text(value: str, *, maximum: int) -> str:
    cleaned = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    if not cleaned:
        raise ValueError("Candidate text is required")
    if len(cleaned) > maximum:
        raise ValueError("Candidate text is too long")
    return cleaned


def normalize_candidate_text(value: str) -> str:
    cleaned = clean_candidate_text(value, maximum=MAX_TEMPLATE_CANDIDATE_VALUE_LENGTH)
    return re.sub(r"\s+", "", cleaned).casefold()


def normalize_candidate_target(target_path: str) -> str:
    """Collapse concrete repeated-row paths into a stable template-level path."""
    return re.sub(r"\.\d+(?=\.|$)", "[]", target_path.strip())


def canonical_managed_candidate(
    value: str | None,
    candidates: tuple[str, ...],
    allowed_suffixes: tuple[str, ...] = (),
) -> str | None:
    if value is None:
        return None
    try:
        normalized_value = normalize_candidate_text(value)
    except ValueError:
        return None
    for candidate in candidates:
        try:
            normalized_candidate = normalize_candidate_text(candidate)
        except ValueError:
            continue
        if normalized_value == normalized_candidate:
            return candidate
        for suffix in allowed_suffixes:
            try:
                normalized_with_suffix = normalize_candidate_text(f"{candidate}{suffix}")
            except ValueError:
                continue
            if normalized_value == normalized_with_suffix:
                return f"{candidate}{suffix}"
    return None
