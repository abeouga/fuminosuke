"""Make job idempotency keys race-safe."""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_unique_job_idempotency_key"
down_revision: str | None = "0005_sire_name_pool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Older releases allowed duplicate non-null keys. Keep the oldest request
    # addressable and clear later duplicates before adding the unique index.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY idempotency_key
                    ORDER BY created_at, id
                ) AS duplicate_order
            FROM processing_jobs
            WHERE idempotency_key IS NOT NULL
        )
        UPDATE processing_jobs
        SET idempotency_key = NULL
        WHERE id IN (
            SELECT id
            FROM ranked
            WHERE duplicate_order > 1
        )
        """
    )
    op.drop_index(
        "ix_processing_jobs_idempotency_key",
        table_name="processing_jobs",
    )
    op.create_index(
        "ix_processing_jobs_idempotency_key",
        "processing_jobs",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processing_jobs_idempotency_key",
        table_name="processing_jobs",
    )
    op.create_index(
        "ix_processing_jobs_idempotency_key",
        "processing_jobs",
        ["idempotency_key"],
        unique=False,
    )
