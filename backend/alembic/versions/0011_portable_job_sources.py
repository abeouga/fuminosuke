"""Store the minimum source images required to rebuild OCR previews."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_portable_job_sources"
down_revision: str | None = "0010_spreadsheet_edit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("template_snapshot_bytes", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "job_inputs",
        sa.Column("content_bytes", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_inputs", "content_bytes")
    op.drop_column("processing_jobs", "template_snapshot_bytes")
