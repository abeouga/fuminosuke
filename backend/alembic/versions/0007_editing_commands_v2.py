"""Persist v2 execution fences and receipts, without storing instruction text."""

import sqlalchemy as sa

from alembic import op

revision = "editing_commands_v2"
down_revision = "editing_voice_streams_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "editing_command_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("editing_sessions.id"), nullable=False
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("instruction_id", sa.String(36), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("command_kind", sa.String(30), nullable=False),
        sa.Column("resource_kind", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("binding_json", sa.JSON(), nullable=False),
        sa.Column("after_command_id", sa.String(36), sa.ForeignKey("editing_command_requests.id")),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("change_digest", sa.String(64)),
        sa.Column("receipt_key", sa.String(128), sa.ForeignKey("mutation_requests.key")),
        sa.Column("accepted_event_json", sa.JSON(), nullable=False),
        sa.Column("final_event_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "request_id", name="uq_editing_command_request"),
        sa.CheckConstraint("fence >= 1"),
        sa.CheckConstraint(
            "state IN ('ACCEPTED','PLANNING','VALIDATING','COMMITTING','APPLIED',"
            "'WAITING_INFORMATION','NO_ACTION','CONFLICT','FAILED','CANCELLED',"
            "'EXPIRED','INTERRUPTED')"
        ),
    )
    for column in ("session_id", "instruction_id", "resource_id"):
        op.create_index(
            f"ix_editing_command_requests_{column}", "editing_command_requests", [column]
        )


def downgrade() -> None:
    raise RuntimeError("Preserve v2 requests/receipts and restore a matching backup instead.")
