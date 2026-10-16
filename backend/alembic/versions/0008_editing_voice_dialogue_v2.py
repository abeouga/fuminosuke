"""A durable pre-commit speech continuation hold; no audio/transcript persistence."""

import sqlalchemy as sa

from alembic import op

revision = "editing_voice_dialogue_v2"
down_revision = "editing_commands_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "editing_command_requests",
        sa.Column("held", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("editing_command_requests", "held")
