"""Ralph core schema - standalone service with project registry

Revision ID: 001
Revises:
Create Date: 2026-01-11

Creates all Ralph Loop tables:
- project_registry: Project metadata and DB connection references
- ralph_builds: Build artifacts from all projects
- ralph_review_queue: Review queue with deduplication
- ralph_review_dispatches: Dispatch audit log
- ralph_inspections: Inspection verdicts
- ralph_revisions: Revision requests
- ralph_db_access_log: DB access audit trail
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create ENUMs (with idempotent handling - skip if already exists)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE secrets_provider AS ENUM ('railway', 'aws_secrets_manager', 'gcp_secret_manager', 'vault', 'none');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE db_context_mode AS ENUM ('none', 'metadata_only', 'readonly');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE build_type AS ENUM ('PLAN', 'CODE');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE builder_signal AS ENUM ('READY_FOR_REVIEW', 'NEEDS_WORK', 'DEPLOYED');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE inspection_status AS ENUM ('PENDING', 'PASSED', 'FAILED');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE review_queue_type AS ENUM ('PLAN', 'CODE');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE review_queue_status AS ENUM ('PENDING', 'DISPATCHED', 'COMPLETED', 'FAILED');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE revision_status AS ENUM ('PENDING', 'IN_PROGRESS', 'COMPLETED');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # Table: project_registry
    op.create_table(
        'project_registry',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('project_id', sa.String(64), nullable=False, unique=True, index=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('repo_url', sa.String(512), nullable=True),
        sa.Column('default_branch', sa.String(64), nullable=False, server_default='main'),
        sa.Column('secrets_provider', postgresql.ENUM('railway', 'aws_secrets_manager', 'gcp_secret_manager', 'vault', 'none', name='secrets_provider', create_type=False), nullable=False, server_default='railway'),
        sa.Column('db_connection_ref', sa.String(256), nullable=True),
        sa.Column('db_context_mode', postgresql.ENUM('none', 'metadata_only', 'readonly', name='db_context_mode', create_type=False), nullable=False, server_default='metadata_only'),
        sa.Column('allowed_schemas', postgresql.JSONB, nullable=True),
        sa.Column('allowed_tables', postgresql.JSONB, nullable=True),
        sa.Column('pii_fields', postgresql.JSONB, nullable=True),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Table: ralph_builds
    op.create_table(
        'ralph_builds',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('build_id', sa.String(128), unique=True, nullable=False, index=True),
        sa.Column('project_id', sa.String(64), nullable=False, index=True),
        sa.Column('build_type', postgresql.ENUM('PLAN', 'CODE', name='build_type', create_type=False), nullable=False, server_default='CODE'),
        sa.Column('task_id', sa.String(128), nullable=True, index=True),
        sa.Column('task_description', sa.Text, nullable=True),
        sa.Column('plan_build_id', sa.String(128), nullable=True),
        sa.Column('commit_sha', sa.String(64), nullable=False),
        sa.Column('branch', sa.String(128), nullable=False),
        sa.Column('changed_files', postgresql.JSONB, nullable=True),
        sa.Column('diff_unified', sa.Text, nullable=True),
        sa.Column('diff_source', sa.String(16), nullable=False, server_default='agent'),
        sa.Column('review_bundle', postgresql.JSONB, nullable=True),
        sa.Column('test_command', sa.String(256), nullable=True),
        sa.Column('test_exit_code', sa.Integer, nullable=True),
        sa.Column('test_output_tail', sa.Text, nullable=True),
        sa.Column('coverage', postgresql.JSONB, nullable=True),
        sa.Column('lint_command', sa.String(256), nullable=True),
        sa.Column('lint_exit_code', sa.Integer, nullable=True),
        sa.Column('lint_output_tail', sa.Text, nullable=True),
        sa.Column('builder_signal', postgresql.ENUM('READY_FOR_REVIEW', 'NEEDS_WORK', 'DEPLOYED', name='builder_signal', create_type=False), nullable=False, server_default='READY_FOR_REVIEW'),
        sa.Column('builder_notes', postgresql.JSONB, nullable=True),
        sa.Column('inspection_status', postgresql.ENUM('PENDING', 'PASSED', 'FAILED', name='inspection_status', create_type=False), nullable=False, server_default='PENDING', index=True),
        sa.Column('iteration_count', sa.Integer, nullable=False, server_default='1'),
        sa.Column('iteration_logs', postgresql.JSONB, nullable=True),
        sa.Column('requires_human_approval', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('approval_reason', sa.String(256), nullable=True),
        sa.Column('human_approved_by', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Index for project queries
    op.create_index('ix_ralph_builds_project', 'ralph_builds', ['project_id', 'created_at'])

    # Table: ralph_review_queue
    op.create_table(
        'ralph_review_queue',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('queue_type', postgresql.ENUM('PLAN', 'CODE', name='review_queue_type', create_type=False), nullable=False, index=True),
        sa.Column('build_pk', postgresql.UUID(as_uuid=True), sa.ForeignKey('ralph_builds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('build_id', sa.String(128), nullable=False),
        sa.Column('project_id', sa.String(64), nullable=False, index=True),
        sa.Column('task_id', sa.String(128), nullable=True, index=True),
        sa.Column('priority', sa.Integer, nullable=False, server_default='5'),
        sa.Column('status', postgresql.ENUM('PENDING', 'DISPATCHED', 'COMPLETED', 'FAILED', name='review_queue_status', create_type=False), nullable=False, server_default='PENDING', index=True),
        sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Unique constraint: only one pending review per (project, task, queue_type)
    op.create_index(
        'ix_ralph_review_queue_unique_pending',
        'ralph_review_queue',
        ['project_id', 'task_id', 'queue_type', 'status'],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'")
    )

    # Index for dispatcher queries
    op.create_index(
        'ix_ralph_review_queue_dispatch',
        'ralph_review_queue',
        ['queue_type', 'status', 'priority', 'created_at']
    )

    # Table: ralph_review_dispatches
    op.create_table(
        'ralph_review_dispatches',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('review_queue_pk', postgresql.UUID(as_uuid=True), sa.ForeignKey('ralph_review_queue.id', ondelete='CASCADE'), nullable=False),
        sa.Column('build_id', sa.String(128), nullable=False),
        sa.Column('inspector_model', sa.String(64), nullable=False),
        sa.Column('dispatch_method', sa.String(32), nullable=False),
        sa.Column('api_response_code', sa.Integer, nullable=True),
        sa.Column('api_response_body', sa.Text, nullable=True),
        sa.Column('error_type', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Table: ralph_inspections
    op.create_table(
        'ralph_inspections',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('build_pk', postgresql.UUID(as_uuid=True), sa.ForeignKey('ralph_builds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('build_id', sa.String(128), nullable=False),
        sa.Column('inspector_model', sa.String(64), nullable=False),
        sa.Column('passed', sa.Boolean, nullable=False),
        sa.Column('issues', postgresql.JSONB, nullable=True),
        sa.Column('suggestions', sa.Text, nullable=True),
        sa.Column('confidence', sa.Float, nullable=True),
        sa.Column('raw_response', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Unique constraint: one inspection per (build, inspector_model)
    op.create_unique_constraint(
        'uq_ralph_inspections_build_inspector',
        'ralph_inspections',
        ['build_pk', 'inspector_model']
    )

    # Table: ralph_revisions
    op.create_table(
        'ralph_revisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('build_pk', postgresql.UUID(as_uuid=True), sa.ForeignKey('ralph_builds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('build_id', sa.String(128), nullable=False),
        sa.Column('revision_id', sa.String(128), unique=True, nullable=False),
        sa.Column('feedback_summary', sa.Text, nullable=False),
        sa.Column('priority_fixes', postgresql.JSONB, nullable=False),
        sa.Column('patch_guidance', sa.Text, nullable=True),
        sa.Column('do_not_change', postgresql.JSONB, nullable=True),
        sa.Column('status', postgresql.ENUM('PENDING', 'IN_PROGRESS', 'COMPLETED', name='revision_status', create_type=False), nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Table: ralph_db_access_log
    op.create_table(
        'ralph_db_access_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('project_id', sa.String(64), nullable=False, index=True),
        sa.Column('build_id', sa.String(128), nullable=True),
        sa.Column('access_mode', sa.String(32), nullable=False),
        sa.Column('query_count', sa.Integer, nullable=False),
        sa.Column('row_count', sa.Integer, nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Index for audit queries
    op.create_index(
        'ix_ralph_db_access_log_project_time',
        'ralph_db_access_log',
        ['project_id', 'created_at']
    )


def downgrade() -> None:
    # Drop tables
    op.drop_table('ralph_db_access_log')
    op.drop_table('ralph_revisions')
    op.drop_table('ralph_inspections')
    op.drop_table('ralph_review_dispatches')
    op.drop_table('ralph_review_queue')
    op.drop_table('ralph_builds')
    op.drop_table('project_registry')

    # Drop ENUMs
    op.execute("DROP TYPE IF EXISTS revision_status")
    op.execute("DROP TYPE IF EXISTS review_queue_status")
    op.execute("DROP TYPE IF EXISTS review_queue_type")
    op.execute("DROP TYPE IF EXISTS inspection_status")
    op.execute("DROP TYPE IF EXISTS builder_signal")
    op.execute("DROP TYPE IF EXISTS build_type")
    op.execute("DROP TYPE IF EXISTS db_context_mode")
    op.execute("DROP TYPE IF EXISTS secrets_provider")
