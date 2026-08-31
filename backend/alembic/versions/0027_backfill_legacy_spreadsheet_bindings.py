"""Backfill current confirmed spreadsheet bindings into library documents."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027_backfill_sheet_bindings"
down_revision: str | None = "0026_drive_change_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE ledger_documents
        SET
            spreadsheet_id = (
                SELECT confirmed_results.spreadsheet_id
                FROM confirmed_results
                WHERE confirmed_results.job_id = ledger_documents.current_job_id
            ),
            spreadsheet_url = COALESCE(
                spreadsheet_url,
                (
                    SELECT confirmed_results.spreadsheet_url
                    FROM confirmed_results
                    WHERE confirmed_results.job_id = ledger_documents.current_job_id
                )
            ),
            spreadsheet_sync_status = COALESCE(
                spreadsheet_sync_status,
                (
                    SELECT confirmed_results.spreadsheet_sync_status
                    FROM confirmed_results
                    WHERE confirmed_results.job_id = ledger_documents.current_job_id
                )
            ),
            spreadsheet_sync_error = COALESCE(
                spreadsheet_sync_error,
                (
                    SELECT confirmed_results.spreadsheet_sync_error
                    FROM confirmed_results
                    WHERE confirmed_results.job_id = ledger_documents.current_job_id
                )
            ),
            library_name = COALESCE(
                library_name,
                (
                    SELECT confirmed_results.spreadsheet_name
                    FROM confirmed_results
                    WHERE confirmed_results.job_id = ledger_documents.current_job_id
                )
            ),
            document_name = COALESCE(
                document_name,
                (
                    SELECT confirmed_results.spreadsheet_name
                    FROM confirmed_results
                    WHERE confirmed_results.job_id = ledger_documents.current_job_id
                )
            )
        WHERE spreadsheet_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM confirmed_results
              WHERE confirmed_results.job_id = ledger_documents.current_job_id
                AND confirmed_results.spreadsheet_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM ledger_documents AS existing_document
                    WHERE existing_document.id != ledger_documents.id
                      AND existing_document.spreadsheet_id = confirmed_results.spreadsheet_id
                )
          )
        """
    )


def downgrade() -> None:
    # The copied values are valid document bindings. Removing them on downgrade
    # would disconnect existing Drive outputs, so this data migration is retained.
    pass
