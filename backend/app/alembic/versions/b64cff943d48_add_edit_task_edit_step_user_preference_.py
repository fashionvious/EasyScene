"""add edit_task edit_step user_preference tables

Revision ID: b64cff943d48
Revises: 5d7e9f1a3b2c
Create Date: 2026-06-01 16:39:16.653859

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = 'b64cff943d48'
down_revision = '5d7e9f1a3b2c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- edit_task ---
    op.create_table(
        'edit_task',
        sa.Column('status', sa.VARCHAR(length=20), nullable=False,
                  server_default=sa.text("'planning'")),
        sa.Column('current_step', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('total_steps', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('project_state_json', sa.TEXT(), nullable=True),
        sa.Column('error_message', sa.TEXT(), nullable=True),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('user_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('script_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('create_time', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('update_time', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('is_deleted', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.ForeignKeyConstraint(['script_id'], ['script.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- edit_step ---
    op.create_table(
        'edit_step',
        sa.Column('step_index', sa.Integer(), nullable=False),
        sa.Column('tool_name', sa.VARCHAR(length=100), nullable=False),
        sa.Column('args_json', sa.TEXT(), nullable=True),
        sa.Column('status', sa.VARCHAR(length=20), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column('result_json', sa.TEXT(), nullable=True),
        sa.Column('error', sa.TEXT(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('task_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('create_time', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('update_time', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['task_id'], ['edit_task.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- user_preference ---
    op.create_table(
        'user_preference',
        sa.Column('preferred_resolution', sa.VARCHAR(length=20), nullable=True),
        sa.Column('preferred_fps', sa.Integer(), nullable=True),
        sa.Column('preferred_speaker', sa.VARCHAR(length=50), nullable=True),
        sa.Column('frequent_media_paths_json', sa.TEXT(), nullable=True),
        sa.Column('last_project_name', sa.VARCHAR(length=255), nullable=True),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('user_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('create_time', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('update_time', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )


def downgrade() -> None:
    op.drop_table('user_preference')
    op.drop_table('edit_step')
    op.drop_table('edit_task')
