"""Add CashierShift table for opening fund tracking."""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cashier_shift_001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create cashier_shift table
    op.create_table(
        'cashier_shift',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cashier_id', sa.Integer(), nullable=False),
        sa.Column('shift_date', sa.Date(), nullable=False),
        sa.Column('shift_start_time', sa.DateTime(), nullable=False),
        sa.Column('shift_end_time', sa.DateTime(), nullable=True),
        sa.Column('opening_fund', sa.Float(), nullable=False),
        sa.Column('closing_fund', sa.Float(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['cashier_id'], ['user.id']),
    )


def downgrade():
    # Drop cashier_shift table
    op.drop_table('cashier_shift')
