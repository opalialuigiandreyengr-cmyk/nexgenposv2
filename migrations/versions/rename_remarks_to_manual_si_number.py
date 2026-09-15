"""Rename remarks column to manual_si_number in Settlement model

Revision ID: rename_remarks_to_manual_si
Revises: 
Create Date: 2026-01-22 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'rename_remarks_to_manual_si'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Rename the remarks column to manual_si_number (if remarks exists)
    try:
        with op.batch_alter_table('settlement', schema=None) as batch_op:
            batch_op.alter_column('remarks', new_column_name='manual_si_number')
    except KeyError:
        # Column doesn't exist, skip
        pass


def downgrade():
    # Rename back to remarks
    with op.batch_alter_table('settlement', schema=None) as batch_op:
        batch_op.alter_column('manual_si_number', new_column_name='remarks')
