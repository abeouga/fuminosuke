"""Add stable ledger documents and page history."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_ledger_document_pages"
down_revision: str | None = "0013_output_layout_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("individual_id", sa.String(length=10), nullable=False),
        sa.Column("current_job_id", sa.String(length=36), nullable=True),
        sa.Column("current_document_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_documents_template_id", "ledger_documents", ["template_id"])
    op.create_index("ix_ledger_documents_individual_id", "ledger_documents", ["individual_id"])
    op.create_index(
        "ix_ledger_documents_identity", "ledger_documents", ["template_id", "individual_id"]
    )
    op.create_table(
        "ledger_document_pages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ledger_document_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=24), nullable=False),
        sa.Column("replaces_page_id", sa.String(length=36), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("page_content_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["ledger_document_id"], ["ledger_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["replaces_page_id"], ["ledger_document_pages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_ledger_document_pages_job_id"),
    )
    op.create_index(
        "ix_ledger_document_pages_ledger_document_id",
        "ledger_document_pages",
        ["ledger_document_id"],
    )
    op.create_index("ix_ledger_document_pages_job_id", "ledger_document_pages", ["job_id"])
    op.create_index(
        "ix_ledger_document_pages_position",
        "ledger_document_pages",
        ["ledger_document_id", "page_number"],
    )


def downgrade() -> None:
    op.drop_table("ledger_document_pages")
    op.drop_table("ledger_documents")
