"""Add Drive folder settings and per-document spreadsheet bindings."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_drive_document_library"
down_revision: str | None = "0014_ledger_document_pages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("google_oauth_accounts") as batch:
        batch.add_column(sa.Column("drive_root_folder_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("drive_root_folder_name", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column("default_output_folder_id", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("default_output_folder_name", sa.String(length=255), nullable=True)
        )
        batch.add_column(sa.Column("default_output_folder_path", sa.Text(), nullable=True))
    with op.batch_alter_table("ledger_documents") as batch:
        batch.add_column(sa.Column("spreadsheet_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("spreadsheet_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("drive_folder_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("drive_folder_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("spreadsheet_sync_status", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("spreadsheet_sync_error", sa.Text(), nullable=True))
        batch.create_unique_constraint(
            "uq_ledger_documents_spreadsheet_id", ["spreadsheet_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("ledger_documents") as batch:
        batch.drop_constraint("uq_ledger_documents_spreadsheet_id", type_="unique")
        batch.drop_column("spreadsheet_sync_error")
        batch.drop_column("spreadsheet_sync_status")
        batch.drop_column("drive_folder_path")
        batch.drop_column("drive_folder_id")
        batch.drop_column("spreadsheet_url")
        batch.drop_column("spreadsheet_id")
    with op.batch_alter_table("google_oauth_accounts") as batch:
        batch.drop_column("default_output_folder_path")
        batch.drop_column("default_output_folder_name")
        batch.drop_column("default_output_folder_id")
        batch.drop_column("drive_root_folder_name")
        batch.drop_column("drive_root_folder_id")
