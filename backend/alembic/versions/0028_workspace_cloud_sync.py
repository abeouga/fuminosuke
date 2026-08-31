"""Add automatic Google Drive workspace snapshot state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_workspace_cloud_sync"
down_revision: str | None = "0027_backfill_sheet_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_cloud_sync_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("google_subject", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DISABLED"),
        sa.Column("stage", sa.String(length=32), nullable=False, server_default="IDLE"),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("dirty", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("remote_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_snapshot_file_id", sa.String(length=255), nullable=True),
        sa.Column("latest_snapshot_sha256", sa.String(length=64), nullable=True),
        sa.Column("latest_snapshot_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner_device_id", sa.String(length=64), nullable=True),
        sa.Column("lease_owner_device_name", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("workspace_cloud_sync_states")
