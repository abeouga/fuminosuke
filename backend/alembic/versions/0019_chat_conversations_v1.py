"""Chat conversations kept on the server, so every device of the user sees them."""

from alembic import op
from app.db.models import ChatConversation

revision = "chat_conversations_v1"
down_revision = "api_call_purpose_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ChatConversation.__table__.create(op.get_bind())  # type: ignore[attr-defined]


def downgrade() -> None:
    op.drop_table("chat_conversations")
