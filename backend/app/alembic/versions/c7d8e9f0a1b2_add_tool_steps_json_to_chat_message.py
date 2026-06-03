"""add tool_steps_json column to chat_message

Revision ID: c7d8e9f0a1b2
Revises: b64cff943d48
Create Date: 2026-06-02 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = 'c7d8e9f0a1b2'
down_revision = 'b64cff943d48'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'chat_message',
        sa.Column('tool_steps_json', sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )


def downgrade():
    op.drop_column('chat_message', 'tool_steps_json')
