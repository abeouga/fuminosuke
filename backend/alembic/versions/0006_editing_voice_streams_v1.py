"""Bound continuous audio time, item identity and commit holds."""

import sqlalchemy as sa

from alembic import op

revision = "editing_voice_streams_v1"
down_revision = "editing_api_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "editing_voice_streams",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("editing_sessions.id"), nullable=False
        ),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("reserved_seconds", sa.Integer(), nullable=False),
        sa.Column("charged_seconds", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("connect_call_id", sa.String(36), sa.ForeignKey("api_call_ledger.id")),
        sa.CheckConstraint("reserved_seconds BETWEEN 1 AND 300"),
        sa.CheckConstraint("charged_seconds BETWEEN 1 AND reserved_seconds"),
        sa.CheckConstraint("state IN ('CREATED','CONNECTED','CLOSED')"),
    )
    op.create_index("ix_editing_voice_streams_session_id", "editing_voice_streams", ["session_id"])
    op.create_table(
        "editing_voice_audio_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "stream_id", sa.String(36), sa.ForeignKey("editing_voice_streams.id"), nullable=False
        ),
        sa.Column("audio_id", sa.String(100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("binding_json", sa.JSON(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("held", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("api_call_ledger.id")),
        sa.Column("upstream_item_id", sa.String(100), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stream_id", "audio_id"),
        sa.UniqueConstraint("stream_id", "sequence"),
        sa.CheckConstraint("sample_count BETWEEN 0 AND 720000"),
        sa.CheckConstraint("state IN ('RECORDING','SUBMITTED','COMPLETED','CANCELLED','FAILED')"),
        sa.CheckConstraint("held IN (0,1)"),
    )
    op.create_index(
        "ix_editing_voice_audio_items_stream_id", "editing_voice_audio_items", ["stream_id"]
    )


def downgrade() -> None:
    raise RuntimeError("Preserve voice budgets and later edits before restoring a matching backup")
