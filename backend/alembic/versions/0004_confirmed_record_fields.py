"""Index fields inside confirmed row records."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_confirmed_record_fields"
down_revision: str | None = "0003_template_layout_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "confirmed_record_fields",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("confirmed_result_id", sa.String(length=36), nullable=False),
        sa.Column("section_key", sa.String(length=100), nullable=False),
        sa.Column("section_title", sa.String(length=200), nullable=False),
        sa.Column("record_order", sa.Integer(), nullable=False),
        sa.Column("record_key", sa.String(length=100), nullable=True),
        sa.Column("field_key", sa.String(length=100), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("numeric_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["confirmed_result_id"],
            ["confirmed_results.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "confirmed_result_id",
            "section_key",
            "record_order",
            "field_key",
            name="uq_confirmed_record_field",
        ),
    )
    op.create_index(
        "ix_confirmed_record_fields_confirmed_result_id",
        "confirmed_record_fields",
        ["confirmed_result_id"],
    )
    op.create_index(
        "ix_confirmed_record_field_lookup",
        "confirmed_record_fields",
        ["section_key", "field_key", "normalized_value"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_confirmed_record_field_lookup",
        table_name="confirmed_record_fields",
    )
    op.drop_index(
        "ix_confirmed_record_fields_confirmed_result_id",
        table_name="confirmed_record_fields",
    )
    op.drop_table("confirmed_record_fields")
