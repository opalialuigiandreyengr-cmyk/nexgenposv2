"""Add modifier column to OrderItem model

Revision ID: add_modifier_order_item
Revises: 
Create Date: 2026-04-16 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_modifier_order_item'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add the modifier column to order_item table
    op.add_column('order_item', sa.Column('modifier', sa.String(255), nullable=True))


def downgrade():
    # Remove the modifier column
    op.drop_column('order_item', 'modifier')
