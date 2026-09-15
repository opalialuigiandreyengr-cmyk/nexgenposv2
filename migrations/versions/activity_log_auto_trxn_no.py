"""Auto-generate transaction numbers for Activity Log

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2025-12-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6g7h8'
down_revision = 'b2c3d4e5f6g7'
branch_labels = None
depends_on = None


def upgrade():
    # Make trxn_no NOT NULL and UNIQUE
    with op.batch_alter_table('activity_log', schema=None) as batch_op:
        # First, populate any NULL trxn_no values with generated ones
        # This ensures we don't have NULL values when setting nullable=False
        
        # Add unique constraint if not exists
        batch_op.alter_column('trxn_no',
                   existing_type=sa.String(20),
                   nullable=False)
        
        # Create unique constraint on trxn_no
        batch_op.create_unique_constraint('uq_activity_log_trxn_no', ['trxn_no'])


def downgrade():
    with op.batch_alter_table('activity_log', schema=None) as batch_op:
        # Remove unique constraint
        batch_op.drop_constraint('uq_activity_log_trxn_no', type_='unique')
        
        # Make trxn_no nullable again
        batch_op.alter_column('trxn_no',
                   existing_type=sa.String(20),
                   nullable=True)
