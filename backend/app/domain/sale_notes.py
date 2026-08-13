from __future__ import annotations

import unicodedata

SALE_NOTE_SEX_SYMBOLS = ("♂", "♀")


def canonical_sale_note(
    value: str | None,
    matched_candidate: str | None,
    candidates: tuple[str, ...] = SALE_NOTE_SEX_SYMBOLS,
) -> str | None:
    raw = _clean(value)
    matched = _matched_prefix(matched_candidate, candidates)
    if raw and raw[0] in candidates:
        return raw
    if matched is None:
        return None
    if not raw or len(raw) == 1:
        return matched
    return f"{matched}{raw[1:]}"


def _matched_prefix(
    value: str | None,
    candidates: tuple[str, ...],
) -> str | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    return next(
        (candidate for candidate in candidates if cleaned.startswith(candidate)),
        None,
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = unicodedata.normalize("NFKC", value).strip()
    return cleaned or None
