"""API ledger purposes such as "editing.v2.voice.connect" are longer than 20 characters.

SQLite never enforced VARCHAR lengths, so only PostgreSQL needs the wider column. The SQLite
table is referenced by other tables and is not rebuilt here.
"""

import sqlalchemy as sa

from alembic import op

revision = "api_call_purpose_length"
down_revision = "template_trash_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("api_call_ledger", "purpose",
                        type_=sa.String(64), existing_type=sa.String(20), existing_nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("api_call_ledger", "purpose",
                        type_=sa.String(20), existing_type=sa.String(64), existing_nullable=False)
