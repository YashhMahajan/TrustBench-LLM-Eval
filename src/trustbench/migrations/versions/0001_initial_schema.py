"""The first schema: everything up to TrustBench 0.1 (runs, scores, annotations, traces, workspaces, jobs).

Later changes to a table go into a new file in this folder, never into this one.

Revision ID: 0001
Revises: nothing
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('key_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=50), nullable=False),
    sa.Column('target', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('dataset_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('case_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'content_hash', 'workspace_id')
    )
    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dataset_versions_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=50), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=True),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(), nullable=False),
    sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
    sa.Column('worker_id', sa.String(length=100), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_run_after'), ['run_after'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('model_configurations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('config_hash', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('system_prompt', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('config_hash')
    )
    op.create_table('proposed_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('dataset_name', sa.String(length=200), nullable=False),
    sa.Column('case_id', sa.String(length=200), nullable=False),
    sa.Column('spec', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('source_run_id', sa.String(length=32), nullable=True),
    sa.Column('source_case_id', sa.String(length=200), nullable=True),
    sa.Column('method', sa.String(length=100), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=True),
    sa.Column('expected_verified', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dataset_name', 'case_id')
    )
    with op.batch_alter_table('proposed_cases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_proposed_cases_dataset_name'), ['dataset_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_proposed_cases_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('workspaces',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('settings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('api_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('prefix', sa.String(length=12), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_keys_prefix'), ['prefix'], unique=False)
        batch_op.create_index(batch_op.f('ix_api_keys_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('evaluation_runs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('dataset_version_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('config_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('software_version', sa.String(length=20), nullable=False),
    sa.Column('total_cost_usd', sa.Float(), nullable=False),
    sa.Column('judge_cost_usd', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['dataset_version_id'], ['dataset_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('evaluation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evaluation_runs_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('production_traces',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('trace_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('duration_ms', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('error_type', sa.String(length=100), nullable=True),
    sa.Column('model', sa.String(length=200), nullable=True),
    sa.Column('prompt_version', sa.String(length=100), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('input_hash', sa.String(length=64), nullable=True),
    sa.Column('output_hash', sa.String(length=64), nullable=True),
    sa.Column('input_chars', sa.Integer(), nullable=True),
    sa.Column('output_chars', sa.Integer(), nullable=True),
    sa.Column('input_enc', sa.Text(), nullable=True),
    sa.Column('output_enc', sa.Text(), nullable=True),
    sa.Column('context_enc', sa.Text(), nullable=True),
    sa.Column('redactions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('truncated', sa.Boolean(), nullable=False),
    sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('feedback', sa.Integer(), nullable=True),
    sa.Column('sampled', sa.Boolean(), nullable=False),
    sa.Column('eval_state', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'trace_id')
    )
    with op.batch_alter_table('production_traces', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_production_traces_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_production_traces_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_production_traces_started_at'), ['started_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_production_traces_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('test_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('dataset_version_id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.String(length=200), nullable=False),
    sa.Column('task', sa.String(length=100), nullable=False),
    sa.Column('input', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('expected', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('evaluation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('meta', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['dataset_version_id'], ['dataset_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dataset_version_id', 'case_id')
    )
    with op.batch_alter_table('test_cases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_test_cases_dataset_version_id'), ['dataset_version_id'], unique=False)

    op.create_table('baselines',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=True),
    sa.Column('run_id', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=250), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['evaluation_runs.id'], ),
    sa.PrimaryKeyConstraint('name')
    )
    with op.batch_alter_table('baselines', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_baselines_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('release_decisions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=250), nullable=False),
    sa.Column('baseline_run_id', sa.String(length=32), nullable=True),
    sa.Column('baseline_model', sa.String(length=250), nullable=True),
    sa.Column('policy_name', sa.String(length=100), nullable=False),
    sa.Column('policy_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('allowed', sa.Boolean(), nullable=False),
    sa.Column('checks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('warnings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('software_version', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['evaluation_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('release_decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_release_decisions_run_id'), ['run_id'], unique=False)

    op.create_table('test_executions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.String(length=32), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=False),
    sa.Column('model_config_id', sa.Integer(), nullable=False),
    sa.Column('repetition', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.ForeignKeyConstraint(['model_config_id'], ['model_configurations.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['evaluation_runs.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['test_cases.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('test_executions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_test_executions_run_id'), ['run_id'], unique=False)

    op.create_table('trace_evaluations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trace_pk', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('evaluator', sa.String(length=100), nullable=False),
    sa.Column('rubric_version', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['trace_pk'], ['production_traces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('trace_evaluations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trace_evaluations_trace_pk'), ['trace_pk'], unique=False)
        batch_op.create_index(batch_op.f('ix_trace_evaluations_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('trace_spans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trace_pk', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('span_id', sa.String(length=64), nullable=False),
    sa.Column('parent_id', sa.String(length=64), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('offset_ms', sa.Float(), nullable=False),
    sa.Column('duration_ms', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('attrs', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['trace_pk'], ['production_traces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('trace_spans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trace_spans_trace_pk'), ['trace_pk'], unique=False)
        batch_op.create_index(batch_op.f('ix_trace_spans_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('model_responses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('execution_id', sa.Integer(), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('output', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('provider_model_id', sa.String(length=200), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['execution_id'], ['test_executions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('model_responses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_model_responses_execution_id'), ['execution_id'], unique=False)

    op.create_table('evaluation_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('response_id', sa.Integer(), nullable=False),
    sa.Column('batch', sa.Integer(), nullable=False),
    sa.Column('evaluator', sa.String(length=100), nullable=False),
    sa.Column('rubric_version', sa.String(length=20), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['response_id'], ['model_responses.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('evaluation_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evaluation_results_response_id'), ['response_id'], unique=False)

    op.create_table('human_annotations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('response_id', sa.Integer(), nullable=False),
    sa.Column('annotator', sa.String(length=100), nullable=False),
    sa.Column('dimension', sa.String(length=100), nullable=False),
    sa.Column('label', sa.Float(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['response_id'], ['model_responses.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('response_id', 'annotator', 'dimension')
    )
    with op.batch_alter_table('human_annotations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_human_annotations_response_id'), ['response_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('human_annotations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_human_annotations_response_id'))

    op.drop_table('human_annotations')
    with op.batch_alter_table('evaluation_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_evaluation_results_response_id'))

    op.drop_table('evaluation_results')
    with op.batch_alter_table('model_responses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_model_responses_execution_id'))

    op.drop_table('model_responses')
    with op.batch_alter_table('trace_spans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_trace_spans_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_trace_spans_trace_pk'))

    op.drop_table('trace_spans')
    with op.batch_alter_table('trace_evaluations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_trace_evaluations_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_trace_evaluations_trace_pk'))

    op.drop_table('trace_evaluations')
    with op.batch_alter_table('test_executions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_test_executions_run_id'))

    op.drop_table('test_executions')
    with op.batch_alter_table('release_decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_release_decisions_run_id'))

    op.drop_table('release_decisions')
    with op.batch_alter_table('baselines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_baselines_workspace_id'))

    op.drop_table('baselines')
    with op.batch_alter_table('test_cases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_test_cases_dataset_version_id'))

    op.drop_table('test_cases')
    with op.batch_alter_table('production_traces', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_production_traces_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_production_traces_started_at'))
        batch_op.drop_index(batch_op.f('ix_production_traces_name'))
        batch_op.drop_index(batch_op.f('ix_production_traces_expires_at'))

    op.drop_table('production_traces')
    with op.batch_alter_table('evaluation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_evaluation_runs_workspace_id'))

    op.drop_table('evaluation_runs')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_keys_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_api_keys_prefix'))

    op.drop_table('api_keys')
    op.drop_table('workspaces')
    with op.batch_alter_table('proposed_cases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_proposed_cases_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_proposed_cases_dataset_name'))

    op.drop_table('proposed_cases')
    op.drop_table('model_configurations')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_jobs_state'))
        batch_op.drop_index(batch_op.f('ix_jobs_run_after'))
        batch_op.drop_index(batch_op.f('ix_jobs_kind'))

    op.drop_table('jobs')
    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dataset_versions_workspace_id'))

    op.drop_table('dataset_versions')
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_workspace_id'))

    op.drop_table('audit_log')
