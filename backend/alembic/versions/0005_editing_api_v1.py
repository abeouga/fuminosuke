"""Bound editing sessions, turns and versioned metadata for shared history."""

import sqlalchemy as sa

from alembic import op

revision = "editing_api_v1"
down_revision = "assistant_voice_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "editing_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("resource_kind", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("view_instance_id", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("context_revision >= 1"),
    )
    op.create_index("ix_editing_sessions_resource_id", "editing_sessions", ["resource_id"])
    op.create_table(
        "editing_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("editing_sessions.id"), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        sa.Column("binding_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("consumed_request_id", sa.String(128), unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_editing_turn_sequence"),
        sa.CheckConstraint("state IN ('RESOLVING','READY','CANCELLED','CONSUMED')"),
    )
    op.create_table(
        "editing_operations",
        sa.Column(
            "operation_id", sa.String(36), sa.ForeignKey("resource_operations.id"), primary_key=True
        ),
        sa.Column("contract_version", sa.String(40), nullable=False),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.Column("turn_id", sa.String(36), sa.ForeignKey("editing_turns.id")),
    )


def downgrade() -> None:
    raise RuntimeError("Preserve later edits before restoring a matching code/database backup")
