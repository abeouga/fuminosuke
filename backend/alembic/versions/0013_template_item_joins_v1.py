"""Items joined into one value on the result screen (display only, per template series)."""

from alembic import op
from app.db.models import TemplateItemJoin

revision = "template_item_joins_v1"
down_revision = "template_display_names_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    TemplateItemJoin.__table__.create(op.get_bind())  # type: ignore[attr-defined]


def downgrade() -> None:
    op.drop_table("template_item_joins")
