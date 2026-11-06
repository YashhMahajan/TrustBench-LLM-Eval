"""Experiments: named variants with a hypothesis and a parent.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=250), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("experiments", schema=None) as b:
        b.create_index("ix_experiments_workspace_name", ["workspace_id", "name"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("experiments", schema=None) as b:
        b.drop_index("ix_experiments_workspace_name")
    op.drop_table("experiments")
