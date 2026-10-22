"""Memory of writing rules learned at confirmation; replaces the reading instruction.

Each item's former reading instruction becomes one memory rule of that item.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.db.models import TemplateFieldMemory, TemplateFieldMemoryRun

revision = "template_field_memories_v1"
down_revision = "template_document_names_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    TemplateFieldMemory.__table__.create(bind)  # type: ignore[attr-defined]
    TemplateFieldMemoryRun.__table__.create(bind)  # type: ignore[attr-defined]
    hints = sa.table(
        "template_field_hints",
        sa.column("template_id", sa.String),
        sa.column("target_patterns_json", sa.JSON),
        sa.column("field_label", sa.String),
        sa.column("instruction", sa.Text),
    )
    memories = TemplateFieldMemory.__table__
    now = datetime.now(UTC)
    rows = []
    for template_id, targets, label, instruction in bind.execute(
        sa.select(
            hints.c.template_id,
            hints.c.target_patterns_json,
            hints.c.field_label,
            hints.c.instruction,
        )
    ):
        text = (instruction or "").strip()
        if not text or not isinstance(targets, list):
            continue
        patterns = [target for target in targets if isinstance(target, str) and target]
        if patterns:
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "template_id": template_id,
                    "target_patterns_json": patterns,
                    "field_label": (label or "")[:100],
                    "rule": text[:200],
                    "hit_count": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    if rows:
        op.bulk_insert(memories, rows)  # type: ignore[arg-type]


def downgrade() -> None:
    op.drop_table("template_field_memory_runs")
    op.drop_table("template_field_memories")
