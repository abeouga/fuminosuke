"""Store generic template definition draft, human revision, and approval history."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_template_definition_revisions"
down_revision: str | None = "0011_portable_job_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_definition_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_version_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_version_id",
            "revision",
            name="uq_template_definition_revision",
        ),
    )
    op.create_index(
        "ix_template_definition_revisions_template_version_id",
        "template_definition_revisions",
        ["template_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_template_definition_latest",
        "template_definition_revisions",
        ["template_version_id", "revision"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_template_definition_latest",
        table_name="template_definition_revisions",
    )
    op.drop_index(
        "ix_template_definition_revisions_template_version_id",
        table_name="template_definition_revisions",
    )
    op.drop_table("template_definition_revisions")
