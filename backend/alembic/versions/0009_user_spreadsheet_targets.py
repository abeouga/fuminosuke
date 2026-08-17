"""Store the spreadsheet created for each Google OAuth account."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_user_spreadsheet_targets"
down_revision: str | None = "0008_google_oauth_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "google_oauth_accounts",
        sa.Column("spreadsheet_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "google_oauth_accounts",
        sa.Column("spreadsheet_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("google_oauth_accounts", "spreadsheet_url")
    op.drop_column("google_oauth_accounts", "spreadsheet_id")
