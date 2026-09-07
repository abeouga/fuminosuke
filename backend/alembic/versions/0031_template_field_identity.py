"""Add version-scoped logical field provenance without rewriting old snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0031_template_field_identity"
down_revision = "0030_template_package_drive_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "template_logical_fields",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "template_id",
            sa.String(36),
            sa.ForeignKey("templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_table(
        "template_field_identities",
        sa.Column(
            "template_version_id",
            sa.String(36),
            sa.ForeignKey("template_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mapping_id", sa.String(220), primary_key=True),
        sa.Column(
            "logical_field_id",
            sa.String(36),
            sa.ForeignKey("template_logical_fields.id"),
            nullable=False,
        ),
        sa.Column("present", sa.Boolean(), nullable=False),
    )
    # Legacy IDs are resolved from (version UUID, exact mapping ID) on read,
    # and materialized on the first fork. Never infer cross-version semantics.


def downgrade() -> None:
    op.drop_table("template_field_identities")
    op.drop_table("template_logical_fields")
