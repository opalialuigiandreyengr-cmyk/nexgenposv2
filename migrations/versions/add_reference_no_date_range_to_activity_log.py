"""add reference_no and date_range to activity_log

Revision ID: add_ref_date_activity
Revises: 
Create Date: 2026-01-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_ref_date_activity'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add reference_no column to activity_log table
    op.add_column('activity_log', sa.Column('reference_no', sa.String(length=50), nullable=True))
    
    # Add date_range column to activity_log table
    op.add_column('activity_log', sa.Column('date_range', sa.String(length=100), nullable=True))


def downgrade():
    # Remove columns if needed
    op.drop_column('activity_log', 'date_range')
    op.drop_column('activity_log', 'reference_no')
