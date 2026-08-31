"""Persist Drive change tokens for incremental library synchronization."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_drive_change_token"
down_revision: str | None = "0025_sheet_edit_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_library_sync_states") as batch:
        batch.add_column(sa.Column("drive_change_token", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "drive_roots_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("document_library_sync_states") as batch:
        batch.drop_column("drive_roots_json")
        batch.drop_column("drive_change_token")
