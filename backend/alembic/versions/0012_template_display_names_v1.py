"""Names given to items on the result screen (display only, per template series)."""

from alembic import op
from app.db.models import TemplateDisplayName

revision = "template_display_names_v1"
down_revision = "template_field_hints_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    TemplateDisplayName.__table__.create(op.get_bind())  # type: ignore[attr-defined]


def downgrade() -> None:
    op.drop_table("template_display_names")
