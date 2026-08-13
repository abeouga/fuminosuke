"""Repair confirmed individual IDs using corrected normalized readings first."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0007_repair_confirmed_individual_ids"
down_revision: str | None = "0006_unique_job_idempotency_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, confirmed_json FROM confirmed_results")
    ).mappings()
    for row in rows:
        document = _as_document(row["confirmed_json"])
        individual_id = _individual_id(document)
        connection.execute(
            sa.text(
                """
                UPDATE confirmed_results
                SET individual_id = :individual_id
                WHERE id = :confirmed_result_id
                """
            ),
            {
                "individual_id": individual_id,
                "confirmed_result_id": row["id"],
            },
        )


def downgrade() -> None:
    # This migration only repairs derived index data. Keeping the corrected
    # value is safer than restoring a known-invalid lookup key.
    return


def _as_document(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _individual_id(document: dict[str, Any]) -> str | None:
    identity = document.get("document")
    if not isinstance(identity, dict):
        return None
    reading = identity.get("unifiedEarTag")
    if not isinstance(reading, dict):
        return None
    for key in ("normalized", "raw"):
        value = reading.get(key)
        if not isinstance(value, str):
            continue
        compact = re.sub(
            r"[\s\u2010\u2011\u2012\u2013\u2014\u2015\u30fc\u2212-]+",
            "",
            unicodedata.normalize("NFKC", value),
        )
        if re.fullmatch(r"\d{10}", compact):
            return compact
    return None
