"""Persist V2 detected cells and semantic layout relationships."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_template_layout_v2"
down_revision: str | None = "0002_individual_record_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_versions",
        sa.Column("layout_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("template_versions", "layout_json")
