"""Item joins: a display-order-only kind and a separator between joined items."""

import sqlalchemy as sa

from alembic import op

revision = "template_item_joins_v2"
down_revision = "template_item_joins_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # v1 creates the table from the current model, which already has these columns on a new DB.
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("template_item_joins")}
    columns = [
        column for column in (
            sa.Column("kind", sa.String(10), nullable=False, server_default="join"),
            sa.Column("separator", sa.String(10), nullable=False, server_default=""),
        )
        if column.name not in existing
    ]
    if not columns:
        return
    with op.batch_alter_table("template_item_joins") as batch:
        for column in columns:
            batch.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("template_item_joins") as batch:
        batch.drop_column("separator")
        batch.drop_column("kind")
