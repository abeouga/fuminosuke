"""The template trash, and documents moved to another template when theirs was deleted."""

from alembic import op
from app.db.models import DocumentTemplateMove, TemplateTrash

revision = "template_trash_v1"
down_revision = "template_field_memories_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    TemplateTrash.__table__.create(op.get_bind())  # type: ignore[attr-defined]
    DocumentTemplateMove.__table__.create(op.get_bind())  # type: ignore[attr-defined]


def downgrade() -> None:
    op.drop_table("document_template_moves")
    op.drop_table("template_trash")
