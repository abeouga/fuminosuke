"""Durable document-wide call reservations, without updating accepted jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0034_paged_ocr_budget"
down_revision = "0033_paged_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paged_ocr_budgets",
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.Column("used_calls", sa.Integer(), nullable=False),
        sa.CheckConstraint("max_calls > 0 AND used_calls >= 0 AND used_calls <= max_calls"),
    )
    op.create_table(
        "paged_ocr_calls",
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("paged_ocr_budgets.job_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("call_number", sa.Integer(), primary_key=True),
        sa.Column("page_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("call_number > 0 AND attempt_number > 0"),
    )


def downgrade() -> None:
    op.drop_table("paged_ocr_calls")
    op.drop_table("paged_ocr_budgets")
