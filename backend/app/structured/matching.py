"""Matching written values and names the way the chat does (shared with the structured APIs)."""

from __future__ import annotations

import re
import unicodedata

_KANA = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}


def fold(value: str) -> str:
    """For matching only: width, case and katakana/hiragana (「コヨミ」 is 「こよみ」) folded."""
    return unicodedata.normalize("NFKC", value).casefold().translate(_KANA)


def compact(value: str) -> str:
    """For matching a written value: folded, without spaces (「田中 太郎」 is 「田中太郎」)."""
    return re.sub(r"\s+", "", fold(value))


def value_equals(written: str | None, wanted: str) -> bool:
    """Whether a field's value is the wanted one (compared compacted). A blank never matches."""
    return written is not None and compact(written) == compact(wanted)
