"""Immutable all-page confirmations; legacy results and document tables are unchanged."""

import sqlalchemy as sa

from alembic import op

revision = "0033_paged_confirmation"
down_revision = "0032_paged_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paged_reviews", sa.Column("document_name", sa.String(180), nullable=True))
    op.create_table(
        "paged_confirmations",
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("paged_reviews.job_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("pages_json", sa.JSON(), nullable=False),
        sa.Column("document_name", sa.String(180), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("paged_confirmations")
    op.drop_column("paged_reviews", "document_name")
