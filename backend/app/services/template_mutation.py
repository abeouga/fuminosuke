from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import RLock
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

LAYOUT_INPUT_REVISION_KEY = "layoutInputRevision"
LAYOUT_VERIFICATION_FINGERPRINT_KEY = "layoutVerificationFingerprint"

_TEMPLATE_MUTATION_LOCK = RLock()


@contextmanager
def template_mutation_lock() -> Iterator[None]:
    """Serialize template state mutations performed by this process."""

    with _TEMPLATE_MUTATION_LOCK:
        yield


def begin_immediate(session: Session) -> None:
    """Take SQLite's write lock before reading state used by a mutation."""

    session.execute(text("BEGIN IMMEDIATE"))


def invalidated_layout_quality(
    quality: Mapping[str, Any],
    *,
    bump_input_revision: bool,
) -> dict[str, Any]:
    updated = {
        **quality,
        "layoutVerified": False,
    }
    updated.pop(LAYOUT_VERIFICATION_FINGERPRINT_KEY, None)
    if bump_input_revision:
        current = quality.get(LAYOUT_INPUT_REVISION_KEY)
        revision = current if isinstance(current, int) and not isinstance(current, bool) else 0
        updated[LAYOUT_INPUT_REVISION_KEY] = revision + 1
    return updated
