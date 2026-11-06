"""Keep the tool calls and spans of app targets, in responses and in the answer cache.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

Json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TABLES = ("model_responses", "response_cache")


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("tool_calls", Json, nullable=True))
            batch_op.add_column(sa.Column("spans", Json, nullable=True))


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("spans")
            batch_op.drop_column("tool_calls")
