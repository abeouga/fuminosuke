"""Add canonical document names and composite identity snapshots."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_document_matching_and_names"
down_revision: str | None = "0022_archive_in_use_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ledger_documents") as batch:
        batch.add_column(sa.Column("document_name", sa.String(length=180), nullable=True))
        batch.add_column(sa.Column("identity_fingerprint", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("identity_snapshot_json", sa.JSON(), nullable=True))
        batch.create_index(
            "ix_ledger_documents_identity_fingerprint",
            ["identity_fingerprint"],
            unique=False,
        )
    op.execute(
        """
        UPDATE ledger_documents
        SET document_name = COALESCE(
            library_name,
            (
                SELECT confirmed_results.spreadsheet_name
                FROM confirmed_results
                WHERE confirmed_results.job_id = ledger_documents.current_job_id
            )
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("ledger_documents") as batch:
        batch.drop_index("ix_ledger_documents_identity_fingerprint")
        batch.drop_column("identity_snapshot_json")
        batch.drop_column("identity_fingerprint")
        batch.drop_column("document_name")
