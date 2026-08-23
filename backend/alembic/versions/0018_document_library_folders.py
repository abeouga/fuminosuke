"""Add virtual folders to the document library."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_document_library_folders"
down_revision: str | None = "0017_template_candidate_sets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_folders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["document_folders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_folders_parent_id", "document_folders", ["parent_id"])
    op.create_index("ix_document_folders_normalized_name", "document_folders", ["normalized_name"])
    with op.batch_alter_table("ledger_documents") as batch:
        batch.add_column(sa.Column("library_folder_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("library_location_version", sa.Integer(), nullable=False, server_default="1"))
        batch.create_foreign_key(
            "fk_ledger_documents_library_folder_id",
            "document_folders",
            ["library_folder_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_ledger_documents_library_folder_id", ["library_folder_id"])


def downgrade() -> None:
    with op.batch_alter_table("ledger_documents") as batch:
        batch.drop_index("ix_ledger_documents_library_folder_id")
        batch.drop_constraint("fk_ledger_documents_library_folder_id", type_="foreignkey")
        batch.drop_column("library_location_version")
        batch.drop_column("library_folder_id")
    op.drop_index("ix_document_folders_normalized_name", table_name="document_folders")
    op.drop_index("ix_document_folders_parent_id", table_name="document_folders")
    op.drop_table("document_folders")
