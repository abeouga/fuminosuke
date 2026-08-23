"""Store idempotent Google Sheets edit events."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_spreadsheet_edit_events"
down_revision: str | None = "0009_user_spreadsheet_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spreadsheet_edit_events",
        sa.Column("edit_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("record_id", sa.String(length=200), nullable=False),
        sa.Column("field_id", sa.String(length=300), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=True),
        sa.Column("source_sheet", sa.String(length=200), nullable=False),
        sa.Column("source_cell", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("edit_id"),
    )
    op.create_index(
        "ix_spreadsheet_edit_events_job_id",
        "spreadsheet_edit_events",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        "ix_spreadsheet_edit_events_status",
        "spreadsheet_edit_events",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_spreadsheet_edit_events_status",
        table_name="spreadsheet_edit_events",
    )
    op.drop_index(
        "ix_spreadsheet_edit_events_job_id",
        table_name="spreadsheet_edit_events",
    )
    op.drop_table("spreadsheet_edit_events")
