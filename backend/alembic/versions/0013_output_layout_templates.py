"""Store Google Sheets output-layout template revision history."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_output_layout_templates"
down_revision: str | None = "0012_template_definition_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "output_layout_template_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_key", sa.String(length=255), nullable=False),
        sa.Column("template_version_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("layout_json", sa.JSON(), nullable=False),
        sa.Column("source_sheet_name", sa.String(length=200), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id"],
            ["processing_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_key",
            "template_version_id",
            "revision",
            name="uq_output_layout_template_revision",
        ),
    )
    op.create_index(
        "ix_output_layout_template_revisions_owner_key",
        "output_layout_template_revisions",
        ["owner_key"],
        unique=False,
    )
    op.create_index(
        "ix_output_layout_template_revisions_template_version_id",
        "output_layout_template_revisions",
        ["template_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_output_layout_template_latest",
        "output_layout_template_revisions",
        ["owner_key", "template_version_id", "revision"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_output_layout_template_latest",
        table_name="output_layout_template_revisions",
    )
    op.drop_index(
        "ix_output_layout_template_revisions_template_version_id",
        table_name="output_layout_template_revisions",
    )
    op.drop_index(
        "ix_output_layout_template_revisions_owner_key",
        table_name="output_layout_template_revisions",
    )
    op.drop_table("output_layout_template_revisions")
