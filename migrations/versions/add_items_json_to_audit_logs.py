"""Add items_json field and make audit log fields nullable for bulk operations

Revision ID: add_items_json
Revises: remove_unique_ref
Create Date: 2025-12-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_items_json'
down_revision = 'remove_unique_ref'
branch_labels = None
depends_on = None


def upgrade():
    # Add items_json column to order_audit_logs
    with op.batch_alter_table('order_audit_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('items_json', sa.Text(), nullable=True))
        # Make product_name, original_quantity, modified_qty, price nullable
        batch_op.alter_column('product_name',
               existing_type=sa.VARCHAR(length=100),
               nullable=True)
        batch_op.alter_column('original_quantity',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.alter_column('modified_qty',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.alter_column('price',
               existing_type=sa.FLOAT(),
               nullable=True)


def downgrade():
    # Remove items_json column and restore NOT NULL constraints
    with op.batch_alter_table('order_audit_logs', schema=None) as batch_op:
        batch_op.drop_column('items_json')
        # Restore NOT NULL constraints
        batch_op.alter_column('product_name',
               existing_type=sa.VARCHAR(length=100),
               nullable=False)
        batch_op.alter_column('original_quantity',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.alter_column('modified_qty',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.alter_column('price',
               existing_type=sa.FLOAT(),
               nullable=False)
