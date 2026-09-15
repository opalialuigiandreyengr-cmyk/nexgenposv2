"""Add x_count column to XReading model

Revision ID: add_x_count
Revises: 
Create Date: 2025-12-22 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_x_count'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add x_count column to xreading table
    op.add_column('x_reading', sa.Column('x_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    # Remove x_count column from xreading table
    op.drop_column('x_reading', 'x_count')
