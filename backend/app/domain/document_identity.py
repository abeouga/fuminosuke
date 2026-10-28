"""The values of the fields a document is told apart by (documentIdentityFields).

A field left blank on the paper is blank: a document is told apart by the fields that have a
value.
"""

from __future__ import annotations

import unicodedata


def identity_text(value: str | None) -> str:
    """The value as identity values are compared: spacing and character width ignored."""
    return "".join(unicodedata.normalize("NFKC", value or "").split()).casefold()
