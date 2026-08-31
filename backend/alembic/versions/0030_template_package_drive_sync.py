"""Add local Google Drive sync state for template packages."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_template_package_drive_sync"
down_revision: str | None = "0029_template_package_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_package_drive_release_syncs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("publisher_id", sa.String(length=253), nullable=False),
        sa.Column("package_id", sa.String(length=100), nullable=False),
        sa.Column("release_version", sa.String(length=100), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("drive_file_id", sa.String(length=255), nullable=False),
        sa.Column("drive_folder_id", sa.String(length=255), nullable=False),
        sa.Column("drive_version", sa.String(length=64), nullable=False),
        sa.Column("drive_head_revision_id", sa.String(length=255), nullable=True),
        sa.Column("drive_etag", sa.Text(), nullable=True),
        sa.Column("drive_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "google_subject",
            "publisher_id",
            "package_id",
            "release_version",
            name="uq_template_package_drive_release_identity",
        ),
        sa.UniqueConstraint(
            "google_subject",
            "drive_file_id",
            name="uq_template_package_drive_release_file",
        ),
    )
    op.create_index(
        "ix_template_package_drive_release_syncs_google_subject",
        "template_package_drive_release_syncs",
        ["google_subject"],
    )
    op.create_index(
        "ix_template_package_drive_release_syncs_publisher_id",
        "template_package_drive_release_syncs",
        ["publisher_id"],
    )
    op.create_index(
        "ix_template_package_drive_release_syncs_package_id",
        "template_package_drive_release_syncs",
        ["package_id"],
    )

    op.create_table(
        "template_package_drive_draft_syncs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("google_subject", sa.String(length=255), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("publisher_id", sa.String(length=253), nullable=False),
        sa.Column("package_id", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_release_version", sa.String(length=100), nullable=True),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("drive_file_id", sa.String(length=255), nullable=False),
        sa.Column("drive_folder_id", sa.String(length=255), nullable=False),
        sa.Column("drive_version", sa.String(length=64), nullable=False),
        sa.Column("drive_head_revision_id", sa.String(length=255), nullable=True),
        sa.Column("drive_etag", sa.Text(), nullable=True),
        sa.Column("drive_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "google_subject",
            "draft_id",
            name="uq_template_package_drive_draft_identity",
        ),
        sa.UniqueConstraint(
            "google_subject",
            "drive_file_id",
            name="uq_template_package_drive_draft_file",
        ),
    )
    op.create_index(
        "ix_template_package_drive_draft_syncs_google_subject",
        "template_package_drive_draft_syncs",
        ["google_subject"],
    )
    op.create_index(
        "ix_template_package_drive_draft_syncs_draft_id",
        "template_package_drive_draft_syncs",
        ["draft_id"],
    )
    op.create_index(
        "ix_template_package_drive_draft_syncs_publisher_id",
        "template_package_drive_draft_syncs",
        ["publisher_id"],
    )
    op.create_index(
        "ix_template_package_drive_draft_syncs_package_id",
        "template_package_drive_draft_syncs",
        ["package_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_template_package_drive_draft_syncs_package_id",
        table_name="template_package_drive_draft_syncs",
    )
    op.drop_index(
        "ix_template_package_drive_draft_syncs_publisher_id",
        table_name="template_package_drive_draft_syncs",
    )
    op.drop_index(
        "ix_template_package_drive_draft_syncs_draft_id",
        table_name="template_package_drive_draft_syncs",
    )
    op.drop_index(
        "ix_template_package_drive_draft_syncs_google_subject",
        table_name="template_package_drive_draft_syncs",
    )
    op.drop_table("template_package_drive_draft_syncs")
    op.drop_index(
        "ix_template_package_drive_release_syncs_package_id",
        table_name="template_package_drive_release_syncs",
    )
    op.drop_index(
        "ix_template_package_drive_release_syncs_publisher_id",
        table_name="template_package_drive_release_syncs",
    )
    op.drop_index(
        "ix_template_package_drive_release_syncs_google_subject",
        table_name="template_package_drive_release_syncs",
    )
    op.drop_table("template_package_drive_release_syncs")
