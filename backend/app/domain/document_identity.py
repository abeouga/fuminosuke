"""The values of the fields a document is told apart by (documentIdentityFields).

A field left blank on the paper is blank: a document is told apart by the fields that have a
value. Before a blank could be confirmed, people wrote a word such as 「なし」 into it; such a
word is read as blank when documents are compared, so it does not have to be written again.
"""

from __future__ import annotations

import unicodedata


def fold(value: str | None) -> str:
    """Spacing and character width ignored, as identity values are compared."""
    return "".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


# Folded with `fold`: 「なし」「無し」「空欄」 and a lone dash.
BLANK_WORDS = frozenset({"なし", "無し", "空欄", "-", "ー", "\u2015", "\u2010"})


def identity_text(value: str | None) -> str:
    """The folded value, or "" for a blank one (empty, or a word written for a blank)."""
    folded = fold(value)
    return "" if folded in BLANK_WORDS else folded
