"""Remove NGRT columns from XReading table

Revision ID: remove_xreading_ngrt
Revises: 
Create Date: 2026-01-24

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'remove_xreading_ngrt'
down_revision = None  # Update this to the latest migration if needed
branch_labels = None
depends_on = None


def upgrade():
    # Remove previous_grand_total and current_grand_total columns from x_reading table (if they exist)
    try:
        with op.batch_alter_table('x_reading', schema=None) as batch_op:
            batch_op.drop_column('previous_grand_total')
    except KeyError:
        pass  # Column doesn't exist
    
    try:
        with op.batch_alter_table('x_reading', schema=None) as batch_op:
            batch_op.drop_column('current_grand_total')
    except KeyError:
        pass  # Column doesn't exist


def downgrade():
    # Restore previous_grand_total and current_grand_total columns if needed to rollback
    with op.batch_alter_table('x_reading', schema=None) as batch_op:
        batch_op.add_column(sa.Column('current_grand_total', sa.Float(), nullable=False, server_default='0.0'))
        batch_op.add_column(sa.Column('previous_grand_total', sa.Float(), nullable=False, server_default='0.0'))
