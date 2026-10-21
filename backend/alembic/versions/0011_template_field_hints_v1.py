"""Per-item reading settings; existing candidate sets become reading settings."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op
from app.db.models import TemplateFieldHint

revision = "template_field_hints_v1"
down_revision = "document_series_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    TemplateFieldHint.__table__.create(bind)  # type: ignore[attr-defined]
    # Each candidate set with its bound fields becomes one setting. The old rule
    # (values outside the list need review) has no setting of its own any more;
    # the list is kept as reference keywords. Suffix combinations stay readable.
    sets = bind.execute(
        sa.text(
            "SELECT id, template_id, name, values_json, allowed_suffixes_json "
            "FROM template_candidate_sets"
        )
    ).fetchall()
    now = datetime.now(UTC)
    for set_id, template_id, name, values_json, suffixes_json in sets:
        targets = [
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT target_pattern FROM template_candidate_bindings "
                    "WHERE candidate_set_id = :id ORDER BY target_pattern"
                ),
                {"id": set_id},
            )
        ]
        if not targets:
            continue
        values = json.loads(values_json) if isinstance(values_json, str) else values_json
        suffixes = json.loads(suffixes_json) if isinstance(suffixes_json, str) else suffixes_json
        keywords = list(dict.fromkeys(
            [*values, *(f"{value}{suffix}" for value in values for suffix in suffixes or [])]
        ))[:500]
        bind.execute(
            TemplateFieldHint.__table__.insert().values(  # type: ignore[attr-defined]
                id=str(uuid4()), template_id=template_id, target_patterns_json=targets,
                field_label=str(name)[:100], keywords_json=keywords, keywords_enabled=True,
                keywords_forced=False, instruction=None, created_at=now, updated_at=now,
            )
        )
    bind.execute(sa.text("DELETE FROM template_candidate_bindings"))
    bind.execute(sa.text("DELETE FROM template_candidate_sets"))


def downgrade() -> None:
    TemplateFieldHint.__table__.drop(op.get_bind())  # type: ignore[attr-defined]
