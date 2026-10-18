"""Document series: a job read as the next version of an earlier confirmed job."""

from alembic import op
from app.db.models import DocumentSeriesLink

revision = "document_series_v1"
down_revision = "template_analysis_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    DocumentSeriesLink.__table__.create(op.get_bind())  # type: ignore[attr-defined]


def downgrade() -> None:
    DocumentSeriesLink.__table__.drop(op.get_bind())  # type: ignore[attr-defined]
