"""Drop order_audit_logs table and migrate data to activity_logs

Revision ID: drop_audit_logs
Revises: remove_unique_ref
Create Date: 2025-12-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'drop_audit_logs'
down_revision = 'remove_unique_ref'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the order_audit_logs table
    op.drop_table('order_audit_logs')


def downgrade():
    # Recreate the order_audit_logs table
    op.create_table(
        'order_audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('product_name', sa.String(100), nullable=False),
        sa.Column('original_quantity', sa.Integer(), nullable=False),
        sa.Column('modified_qty', sa.Integer(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('reason', sa.String(200), nullable=True),
        sa.Column('reference_no', sa.String(20), nullable=True),
        sa.Column('event_type', sa.String(20), server_default='Void'),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('cashier_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], ['order.id'], ),
        sa.ForeignKeyConstraint(['cashier_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
