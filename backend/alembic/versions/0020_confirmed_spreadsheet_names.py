"""Store editable names for confirmed spreadsheet outputs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_confirmed_spreadsheet_names"
down_revision: str | None = "0019_confirmed_spreadsheet_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("confirmed_results") as batch:
        batch.add_column(sa.Column("spreadsheet_name", sa.String(length=180), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("confirmed_results") as batch:
        batch.drop_column("spreadsheet_name")
