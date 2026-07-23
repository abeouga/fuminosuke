"""Index confirmed OCR JSON by cattle individual identification number."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_individual_record_index"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "confirmed_results",
        sa.Column("individual_id", sa.String(length=10), nullable=True),
    )
    op.create_index(
        "ix_confirmed_results_individual_id",
        "confirmed_results",
        ["individual_id"],
    )
    # Preserve already-confirmed data when it contains a valid 10-digit value.
    # New confirmations always populate this column in application code.
    op.execute(
        """
        UPDATE confirmed_results
        SET individual_id = replace(
            replace(
                coalesce(
                    json_extract(confirmed_json, '$.document.unifiedEarTag.raw'),
                    json_extract(confirmed_json, '$.document.unifiedEarTag.normalized')
                ),
                '-',
                ''
            ),
            ' ',
            ''
        )
        WHERE length(
            replace(
                replace(
                    coalesce(
                        json_extract(confirmed_json, '$.document.unifiedEarTag.raw'),
                        json_extract(confirmed_json, '$.document.unifiedEarTag.normalized')
                    ),
                    '-',
                    ''
                ),
                ' ',
                ''
            )
        ) = 10
        AND replace(
            replace(
                coalesce(
                    json_extract(confirmed_json, '$.document.unifiedEarTag.raw'),
                    json_extract(confirmed_json, '$.document.unifiedEarTag.normalized')
                ),
                '-',
                ''
            ),
            ' ',
            ''
        ) NOT GLOB '*[^0-9]*'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_confirmed_results_individual_id", table_name="confirmed_results")
    op.drop_column("confirmed_results", "individual_id")
