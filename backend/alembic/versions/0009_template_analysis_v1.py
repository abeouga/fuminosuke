"""Isolated template analysis runs and facet provenance; no layout conversion."""

from alembic import op
from app.template_analysis.models import TABLES

revision = "template_analysis_v1"
down_revision = "editing_voice_dialogue_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in TABLES:
        table.create(op.get_bind())


def downgrade() -> None:
    for table in reversed(TABLES):
        table.drop(op.get_bind())
