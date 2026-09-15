"""Add timestamp to order_item table

Revision ID: add_timestamp_order_item
Revises: bdd39396095e
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_timestamp_order_item'
down_revision = 'bdd39396095e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('order_item', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timestamp', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('order_item', schema=None) as batch_op:
        batch_op.drop_column('timestamp')
