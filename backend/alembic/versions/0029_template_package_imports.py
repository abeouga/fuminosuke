"""Add self-contained template package identity and installation records."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_template_package_imports"
down_revision: str | None = "0028_workspace_cloud_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_package_identities",
        sa.Column("publisher_id", sa.String(length=253), nullable=False),
        sa.Column("package_id", sa.String(length=100), nullable=False),
        sa.Column("local_template_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["local_template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("publisher_id", "package_id"),
        sa.UniqueConstraint("local_template_id"),
    )
    op.create_index(
        "ix_template_package_identities_local_template_id",
        "template_package_identities",
        ["local_template_id"],
        unique=False,
    )
    op.create_table(
        "template_package_installations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("publisher_id", sa.String(length=253), nullable=False),
        sa.Column("package_id", sa.String(length=100), nullable=False),
        sa.Column("release_version", sa.String(length=100), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("local_template_id", sa.String(length=36), nullable=False),
        sa.Column("local_template_version_id", sa.String(length=36), nullable=False),
        sa.Column("local_job_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False, server_default="FILE"),
        sa.Column(
            "trust_status", sa.String(length=24), nullable=False, server_default="UNVERIFIED"
        ),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("canonical_template_json", sa.JSON(), nullable=False),
        sa.Column("canonical_quality_json", sa.JSON(), nullable=False),
        sa.Column("output_layout_json", sa.JSON(), nullable=True),
        sa.Column("local_mapping_json", sa.JSON(), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["publisher_id", "package_id"],
            [
                "template_package_identities.publisher_id",
                "template_package_identities.package_id",
            ],
            name="fk_template_package_installation_identity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["local_job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["local_template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["local_template_version_id"], ["template_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publisher_id",
            "package_id",
            "release_version",
            name="uq_template_package_installation_release",
        ),
        sa.UniqueConstraint("local_template_version_id"),
    )
    op.create_index(
        "ix_template_package_installations_publisher_id",
        "template_package_installations",
        ["publisher_id"],
        unique=False,
    )
    op.create_index(
        "ix_template_package_installations_package_id",
        "template_package_installations",
        ["package_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_template_package_installations_package_id",
        table_name="template_package_installations",
    )
    op.drop_index(
        "ix_template_package_installations_publisher_id",
        table_name="template_package_installations",
    )
    op.drop_table("template_package_installations")
    op.drop_index(
        "ix_template_package_identities_local_template_id",
        table_name="template_package_identities",
    )
    op.drop_table("template_package_identities")
