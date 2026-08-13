"""Add the managed sire-name OCR candidate pool."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0005_sire_name_pool"
down_revision: str | None = "0004_confirmed_record_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INITIAL_SIRE_NAMES = ("勝忠平", "光晴重", "重波平", "百合茂")


def upgrade() -> None:
    table = op.create_table(
        "sire_names",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name", name="uq_sire_names_normalized_name"),
    )
    op.create_index("ix_sire_names_normalized_name", "sire_names", ["normalized_name"])
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [
            {
                "id": f"00000000-0000-0000-0000-{index:012d}",
                "name": name,
                "normalized_name": name.casefold(),
                "created_at": now,
            }
            for index, name in enumerate(INITIAL_SIRE_NAMES, start=1)
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_sire_names_normalized_name", table_name="sire_names")
    op.drop_table("sire_names")
