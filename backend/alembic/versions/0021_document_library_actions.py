"""Add document library names and soft deletion."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_document_library_actions"
down_revision: str | None = "0020_confirmed_spreadsheet_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ledger_documents") as batch:
        batch.add_column(sa.Column("library_name", sa.String(length=180), nullable=True))
        batch.add_column(sa.Column("library_deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            "ix_ledger_documents_library_deleted_at",
            ["library_deleted_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("ledger_documents") as batch:
        batch.drop_index("ix_ledger_documents_library_deleted_at")
        batch.drop_column("library_deleted_at")
        batch.drop_column("library_name")
