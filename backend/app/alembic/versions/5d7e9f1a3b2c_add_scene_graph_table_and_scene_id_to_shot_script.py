"""add scene_graph table and scene_id to shot_script

Revision ID: 5d7e9f1a3b2c
Revises: 4b6c8d0e2f1a
Create Date: 2026-05-01 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

revision = '5d7e9f1a3b2c'
down_revision = '4b6c8d0e2f1a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'scene_graph',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('scene_group', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('scene_name', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False, server_default='默认场景'),
        sa.Column('scene_image_path', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('create_time', sa.DateTime(), nullable=False),
        sa.Column('update_time', sa.DateTime(), nullable=False),
        sa.Column('is_deleted', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['script_id'], ['script.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column('shot_script', sa.Column('scene_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_shot_script_scene_id', 'shot_script', 'scene_graph', ['scene_id'], ['id'])


def downgrade():
    op.drop_constraint('fk_shot_script_scene_id', 'shot_script', type_='foreignkey')
    op.drop_column('shot_script', 'scene_id')
    op.drop_table('scene_graph')
