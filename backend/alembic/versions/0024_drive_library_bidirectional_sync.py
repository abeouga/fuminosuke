"""Add persistence for bidirectional document-library Drive synchronization."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_drive_library_sync"
down_revision: str | None = "0023_document_matching_and_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicate_root_name = connection.execute(
        sa.text(
            """
            SELECT normalized_name
            FROM document_folders
            WHERE parent_id IS NULL
            GROUP BY normalized_name
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    duplicate_child_name = connection.execute(
        sa.text(
            """
            SELECT parent_id, normalized_name
            FROM document_folders
            WHERE parent_id IS NOT NULL
            GROUP BY parent_id, normalized_name
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_root_name is not None or duplicate_child_name is not None:
        raise RuntimeError(
            "Duplicate document-library folder names must be resolved before "
            "upgrading to 0024_drive_library_sync. No 0024 schema changes were applied."
        )

    op.create_index(
        "uq_document_folders_root_normalized_name",
        "document_folders",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "uq_document_folders_child_normalized_name",
        "document_folders",
        ["parent_id", "normalized_name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL"),
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    op.create_table(
        "document_folder_drive_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("document_folder_id", sa.String(length=36), nullable=False),
        sa.Column("drive_root_folder_id", sa.String(length=255), nullable=False),
        sa.Column("drive_folder_id", sa.String(length=255), nullable=False),
        sa.Column("drive_name", sa.String(length=255), nullable=False),
        sa.Column("drive_parent_folder_id", sa.String(length=255), nullable=False),
        sa.Column("drive_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["google_subject"],
            ["google_oauth_accounts.google_subject"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_folder_id"],
            ["document_folders.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "google_subject",
            "document_folder_id",
            "drive_root_folder_id",
            name="uq_document_folder_drive_mapping_local_root",
        ),
        sa.UniqueConstraint(
            "google_subject",
            "drive_folder_id",
            name="uq_document_folder_drive_mapping_remote",
        ),
    )
    op.create_index(
        "ix_document_folder_drive_mappings_google_subject",
        "document_folder_drive_mappings",
        ["google_subject"],
    )
    op.create_index(
        "ix_document_folder_drive_mappings_document_folder_id",
        "document_folder_drive_mappings",
        ["document_folder_id"],
    )
    op.create_index(
        "ix_document_folder_drive_mappings_drive_root_folder_id",
        "document_folder_drive_mappings",
        ["drive_root_folder_id"],
    )
    op.create_index(
        "ix_document_folder_drive_mappings_drive_parent_folder_id",
        "document_folder_drive_mappings",
        ["drive_parent_folder_id"],
    )

    op.create_table(
        "document_library_sync_states",
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("conflicts_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["google_subject"],
            ["google_oauth_accounts.google_subject"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("google_subject"),
    )

    op.create_table(
        "spreadsheet_output_operations",
        sa.Column("operation_id", sa.String(length=124), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("drive_root_folder_id", sa.String(length=255), nullable=False),
        sa.Column("drive_parent_folder_id", sa.String(length=255), nullable=False),
        sa.Column("drive_name", sa.String(length=180), nullable=False),
        sa.Column("drive_folder_path", sa.Text(), nullable=True),
        sa.Column("library_folder_id", sa.String(length=36), nullable=True),
        sa.Column("library_location_version", sa.Integer(), nullable=False),
        sa.Column("spreadsheet_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ledger_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_spreadsheet_output_operations_document_id",
        "spreadsheet_output_operations",
        ["document_id"],
    )
    op.create_index(
        "ix_spreadsheet_output_operations_google_subject",
        "spreadsheet_output_operations",
        ["google_subject"],
    )

    ledger_column_names = {
        column["name"] for column in sa.inspect(connection).get_columns("ledger_documents")
    }
    ledger_index_names = {
        index["name"] for index in sa.inspect(connection).get_indexes("ledger_documents")
    }
    with op.batch_alter_table("ledger_documents") as batch:
        if "drive_google_subject" not in ledger_column_names:
            batch.add_column(
                sa.Column("drive_google_subject", sa.String(length=255), nullable=True)
            )
        if "drive_root_folder_id" not in ledger_column_names:
            batch.add_column(
                sa.Column("drive_root_folder_id", sa.String(length=255), nullable=True)
            )
        if "drive_ownership_confirmed_at" not in ledger_column_names:
            batch.add_column(
                sa.Column(
                    "drive_ownership_confirmed_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
        if "drive_ownership_confirmation_source" not in ledger_column_names:
            batch.add_column(
                sa.Column(
                    "drive_ownership_confirmation_source",
                    sa.String(length=32),
                    nullable=True,
                )
            )
        if "ix_ledger_documents_drive_google_subject" not in ledger_index_names:
            batch.create_index(
                "ix_ledger_documents_drive_google_subject",
                ["drive_google_subject"],
            )

    ledger_documents = sa.table(
        "ledger_documents",
        sa.column("id", sa.String(length=36)),
        sa.column("template_id", sa.String(length=36)),
        sa.column("drive_folder_id", sa.String(length=255)),
        sa.column("drive_root_folder_id", sa.String(length=255)),
        sa.column("drive_google_subject", sa.String(length=255)),
    )
    op.execute(
        sa.update(ledger_documents)
        .where(
            ledger_documents.c.drive_root_folder_id.is_(None),
            ledger_documents.c.drive_folder_id.is_not(None),
        )
        .values(drive_root_folder_id=ledger_documents.c.drive_folder_id)
    )


def downgrade() -> None:
    op.drop_index(
        "ix_spreadsheet_output_operations_google_subject",
        table_name="spreadsheet_output_operations",
    )
    op.drop_index(
        "ix_spreadsheet_output_operations_document_id",
        table_name="spreadsheet_output_operations",
    )
    op.drop_table("spreadsheet_output_operations")
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # Rebuilding the parent table on SQLite fires ON DELETE CASCADE on
        # ledger_document_pages when Alembic drops the old parent table. Keep
        # the nullable compatibility columns; a later 0024 re-upgrade detects
        # and reuses them instead of risking silent page-history deletion.
        op.drop_index(
            "ix_ledger_documents_drive_google_subject",
            table_name="ledger_documents",
        )
    else:
        with op.batch_alter_table("ledger_documents") as batch:
            batch.drop_index("ix_ledger_documents_drive_google_subject")
            batch.drop_column("drive_ownership_confirmation_source")
            batch.drop_column("drive_ownership_confirmed_at")
            batch.drop_column("drive_root_folder_id")
            batch.drop_column("drive_google_subject")
    op.drop_table("document_library_sync_states")
    op.drop_index(
        "ix_document_folder_drive_mappings_drive_parent_folder_id",
        table_name="document_folder_drive_mappings",
    )
    op.drop_index(
        "ix_document_folder_drive_mappings_drive_root_folder_id",
        table_name="document_folder_drive_mappings",
    )
    op.drop_index(
        "ix_document_folder_drive_mappings_document_folder_id",
        table_name="document_folder_drive_mappings",
    )
    op.drop_index(
        "ix_document_folder_drive_mappings_google_subject",
        table_name="document_folder_drive_mappings",
    )
    op.drop_table("document_folder_drive_mappings")
    op.drop_index(
        "uq_document_folders_child_normalized_name",
        table_name="document_folders",
    )
    op.drop_index(
        "uq_document_folders_root_normalized_name",
        table_name="document_folders",
    )
