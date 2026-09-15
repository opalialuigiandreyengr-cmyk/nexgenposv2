"""Add remarks column to Settlement model

Revision ID: add_remarks_to_settlement
Revises: 
Create Date: 2026-01-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_remarks_to_settlement'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add the remarks column to settlement table
    op.add_column('settlement', sa.Column('remarks', sa.Text(), nullable=True))


def downgrade():
    # Remove the remarks column
    op.drop_column('settlement', 'remarks')
