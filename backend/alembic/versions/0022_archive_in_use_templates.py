"""Archive in-use templates instead of deleting ledger history."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_archive_in_use_templates"
down_revision: str | None = "0021_document_library_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("templates") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            "ix_templates_archived_at",
            ["archived_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("templates") as batch:
        batch.drop_index("ix_templates_archived_at")
        batch.drop_column("archived_at")
