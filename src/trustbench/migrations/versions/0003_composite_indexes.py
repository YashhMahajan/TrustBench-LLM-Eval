"""Indexes that match how the code reads the data.

A composite index answers a query that filters on its first column and sorts or filters
on the next ones. It also replaces the single-column index on its first column, so we
drop those. Fewer indexes means faster writes.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evaluation_runs", schema=None) as b:
        b.drop_index("ix_evaluation_runs_workspace_id")
        b.create_index("ix_evaluation_runs_workspace_created", ["workspace_id", "created_at"], unique=False)
    with op.batch_alter_table("test_executions", schema=None) as b:
        b.drop_index("ix_test_executions_run_id")
        b.create_index("ix_test_executions_run_status", ["run_id", "status"], unique=False)
    with op.batch_alter_table("production_traces", schema=None) as b:
        b.drop_index("ix_production_traces_workspace_id")
        b.create_index("ix_production_traces_workspace_started", ["workspace_id", "started_at"], unique=False)
    with op.batch_alter_table("jobs", schema=None) as b:
        b.drop_index("ix_jobs_state")
        b.drop_index("ix_jobs_run_after")
        b.create_index("ix_jobs_state_run_after_id", ["state", "run_after", "id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as b:
        b.drop_index("ix_jobs_state_run_after_id")
        b.create_index("ix_jobs_run_after", ["run_after"], unique=False)
        b.create_index("ix_jobs_state", ["state"], unique=False)
    with op.batch_alter_table("production_traces", schema=None) as b:
        b.drop_index("ix_production_traces_workspace_started")
        b.create_index("ix_production_traces_workspace_id", ["workspace_id"], unique=False)
    with op.batch_alter_table("test_executions", schema=None) as b:
        b.drop_index("ix_test_executions_run_status")
        b.create_index("ix_test_executions_run_id", ["run_id"], unique=False)
    with op.batch_alter_table("evaluation_runs", schema=None) as b:
        b.drop_index("ix_evaluation_runs_workspace_created")
        b.create_index("ix_evaluation_runs_workspace_id", ["workspace_id"], unique=False)
