"""Item joins: a display-order-only kind and a separator between joined items."""

import sqlalchemy as sa

from alembic import op

revision = "template_item_joins_v2"
down_revision = "template_item_joins_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("template_item_joins") as batch:
        batch.add_column(sa.Column("kind", sa.String(10), nullable=False, server_default="join"))
        batch.add_column(sa.Column("separator", sa.String(10), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("template_item_joins") as batch:
        batch.drop_column("separator")
        batch.drop_column("kind")
