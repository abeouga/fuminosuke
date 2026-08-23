"""Add template-specific OCR candidate sets and field bindings."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_template_candidate_sets"
down_revision: str | None = "0016_template_output_folders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_candidate_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("values_json", sa.JSON(), nullable=False),
        sa.Column("allowed_suffixes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_id",
            "normalized_name",
            name="uq_template_candidate_sets_template_name",
        ),
    )
    op.create_index(
        "ix_template_candidate_sets_template_id",
        "template_candidate_sets",
        ["template_id"],
    )
    op.create_table(
        "template_candidate_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_set_id", sa.String(length=36), nullable=False),
        sa.Column("target_pattern", sa.String(length=200), nullable=False),
        sa.Column("field_label", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["candidate_set_id"],
            ["template_candidate_sets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_id",
            "target_pattern",
            name="uq_template_candidate_bindings_target",
        ),
    )
    op.create_index(
        "ix_template_candidate_bindings_template_id",
        "template_candidate_bindings",
        ["template_id"],
    )
    op.create_index(
        "ix_template_candidate_bindings_candidate_set_id",
        "template_candidate_bindings",
        ["candidate_set_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_template_candidate_bindings_candidate_set_id",
        table_name="template_candidate_bindings",
    )
    op.drop_index(
        "ix_template_candidate_bindings_template_id",
        table_name="template_candidate_bindings",
    )
    op.drop_table("template_candidate_bindings")
    op.drop_index(
        "ix_template_candidate_sets_template_id",
        table_name="template_candidate_sets",
    )
    op.drop_table("template_candidate_sets")
