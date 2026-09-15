"""Add discount_breakdown column to Settlement model

Revision ID: discount_breakdown
Revises: 
Create Date: 2025-12-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'discount_breakdown'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add the discount_breakdown column to settlement table
    op.add_column('settlement', sa.Column('discount_breakdown', sa.Text(), nullable=True))


def downgrade():
    # Remove the discount_breakdown column
    op.drop_column('settlement', 'discount_breakdown')
