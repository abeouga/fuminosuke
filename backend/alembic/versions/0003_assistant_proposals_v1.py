"""Persist bound sessions, proposals and one-use presentation challenges."""

import sqlalchemy as sa

from alembic import op

revision = "assistant_proposals_v1"
down_revision = "authoring_mutations_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("context_revision >= 1"),
    )
    op.create_table(
        "assistant_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("assistant_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("diff_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("challenge_hash", sa.String(64)),
        sa.Column("presented_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_request_key", sa.String(128), sa.ForeignKey("mutation_requests.key")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('PROPOSED','PRESENTED','CONSUMED','CANCELLED','SUPERSEDED','EXPIRED')"
        ),
    )
    op.create_index("ix_assistant_proposals_session_id", "assistant_proposals", ["session_id"])


def downgrade() -> None:
    raise RuntimeError("Restore the matching code, database and artifact backup instead")
