"""Add encrypted Google OAuth account storage."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_google_oauth_accounts"
down_revision: str | None = "0007_repair_confirmed_individual_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_oauth_accounts",
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("google_subject"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(
        op.f("ix_google_oauth_accounts_email"),
        "google_oauth_accounts",
        ["email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_google_oauth_accounts_is_active"),
        "google_oauth_accounts",
        ["is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_google_oauth_accounts_is_active"), table_name="google_oauth_accounts")
    op.drop_index(op.f("ix_google_oauth_accounts_email"), table_name="google_oauth_accounts")
    op.drop_table("google_oauth_accounts")
