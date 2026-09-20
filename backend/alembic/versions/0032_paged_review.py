"""Page-bound review revisions without migrating or rewriting legacy OCR results."""

import sqlalchemy as sa

from alembic import op

revision = "0032_paged_review"
down_revision = "0031_template_field_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paged_reviews",
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("raw_pages_json", sa.JSON(), nullable=False),
        sa.Column("current_pages_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "paged_review_revisions",
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("paged_reviews.job_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("page_id", sa.String(36), nullable=True),
        sa.Column("pages_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("paged_review_revisions")
    op.drop_table("paged_reviews")
