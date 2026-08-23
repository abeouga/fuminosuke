"""Add per-account output folders for templates."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_template_output_folders"
down_revision: str | None = "0015_drive_document_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_output_folders",
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("folder_id", sa.String(length=255), nullable=False),
        sa.Column("folder_name", sa.String(length=255), nullable=False),
        sa.Column("folder_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["google_subject"],
            ["google_oauth_accounts.google_subject"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("google_subject", "template_id"),
    )


def downgrade() -> None:
    op.drop_table("template_output_folders")
