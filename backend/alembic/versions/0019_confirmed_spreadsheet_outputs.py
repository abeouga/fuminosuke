"""Store per-OCR Google spreadsheet outputs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_confirmed_spreadsheet_outputs"
down_revision: str | None = "0018_document_library_folders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("confirmed_results") as batch:
        batch.add_column(sa.Column("spreadsheet_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("spreadsheet_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("spreadsheet_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("spreadsheet_sync_status", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("spreadsheet_sync_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("confirmed_results") as batch:
        batch.drop_column("spreadsheet_sync_error")
        batch.drop_column("spreadsheet_sync_status")
        batch.drop_column("spreadsheet_revision")
        batch.drop_column("spreadsheet_url")
        batch.drop_column("spreadsheet_id")
