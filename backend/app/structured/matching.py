"""Matching written values and names the way the chat does (shared with the structured APIs)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

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


_NUMBERED = re.compile(r"^(.*\D)(\d+)$")


def choice_name(label: str, labels: Sequence[str] | None) -> str:
    """A repeated choice name is stored numbered (黒・白・黒2) only to keep the names apart;
    people see the printed name (黒). Same rule as the review screen's ``choiceName``."""
    match = _NUMBERED.match(label) if labels else None
    if match and labels and label in labels and match.group(1) in labels:
        return match.group(1)
    return label


def shown_value(value: str | None, labels: Sequence[str] | None) -> str | None:
    """A selection field's value as people see it (each 「・」-joined choice by its name)."""
    if value is None or not labels:
        return value
    return "・".join(choice_name(part.strip(), labels) for part in value.split("・"))
