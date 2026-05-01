"""add last_frame_image_path to shot_script

Revision ID: 4b6c8d0e2f1a
Revises: 3a5b7c9d1e2f
Create Date: 2026-05-01 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

revision = '4b6c8d0e2f1a'
down_revision = '3a5b7c9d1e2f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('shot_script', sa.Column('last_frame_image_path', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True))


def downgrade():
    op.drop_column('shot_script', 'last_frame_image_path')
