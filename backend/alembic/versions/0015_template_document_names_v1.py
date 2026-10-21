"""How documents of a template series are named, changeable after the template is made."""

from alembic import op
from app.db.models import TemplateDocumentName

revision = "template_document_names_v1"
down_revision = "template_item_joins_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    TemplateDocumentName.__table__.create(op.get_bind())  # type: ignore[attr-defined]


def downgrade() -> None:
    op.drop_table("template_document_names")
