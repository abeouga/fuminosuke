"""Bind spreadsheet edit idempotency keys to their Sheet target."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_sheet_edit_identity"
down_revision: str | None = "0024_drive_library_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("spreadsheet_edit_events") as batch:
        batch.add_column(sa.Column("spreadsheet_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("google_subject", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("spreadsheet_edit_events") as batch:
        batch.drop_column("google_subject")
        batch.drop_column("spreadsheet_id")
