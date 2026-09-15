"""Remove unique constraint from reference_no in order_audit_logs

Revision ID: remove_unique_ref
Revises: f7734b2038ce
Create Date: 2025-12-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'remove_unique_ref'
down_revision = 'f7734b2038ce'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the unique constraint on reference_no
    with op.batch_alter_table('order_audit_logs', schema=None) as batch_op:
        batch_op.drop_constraint('uq_order_audit_logs_reference_no', type_='unique')


def downgrade():
    # Restore the unique constraint on reference_no
    with op.batch_alter_table('order_audit_logs', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_order_audit_logs_reference_no', ['reference_no'])
