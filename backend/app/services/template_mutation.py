from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import RLock
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import TemplateVersion

LAYOUT_INPUT_REVISION_KEY = "layoutInputRevision"
LAYOUT_VERIFICATION_FINGERPRINT_KEY = "layoutVerificationFingerprint"

_TEMPLATE_MUTATION_LOCK = RLock()


def require_draft(version: TemplateVersion) -> None:
    if version.status != "DRAFT" or version.approved_at is not None:
        raise AppError(status=409, code="TEMPLATE_VERSION_IMMUTABLE",
                       title="Published template cannot be edited",
                       detail="公開済みの版は変更できません。編集から下書きを作成してください。")


def require_isolated_ids(version: TemplateVersion, ids: list[str]) -> None:
    protected = set(version.quality_json.get("protectedPhysicalIds", []))
    if protected.intersection(ids):
        raise AppError(status=409, code="TEMPLATE_PHYSICAL_ID_SHARED",
                       title="Physical identity belongs to an earlier version",
                       detail="過去版の矩形・mapping IDは編集用下書きで再利用できません。")


def require_isolated_structure(version: TemplateVersion, value: Any) -> None:
    ids: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if isinstance(item.get("id"), str):
                ids.append(item["id"])
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    require_isolated_ids(version, ids)


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
