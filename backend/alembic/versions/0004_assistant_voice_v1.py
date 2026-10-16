"""Bound audio turns and a durable, shared 100-call external AI ledger."""

import sqlalchemy as sa

from alembic import op

revision = "assistant_voice_v1"
down_revision = "assistant_proposals_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_call_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ordinal", sa.Integer(), unique=True, nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ordinal >= 1"),
    )
    op.create_table(
        "assistant_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_key", sa.String(128), unique=True, nullable=False),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("assistant_sessions.id"), nullable=False
        ),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("binding_json", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("result_json", sa.JSON()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Restore the matching code, database and artifact backup instead")
