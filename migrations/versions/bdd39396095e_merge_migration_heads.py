"""Merge migration heads

Revision ID: bdd39396095e
Revises: c3d4e5f6g7h8, cashier_shift_001, discount_breakdown, add_gift_check_cheque_numbers, add_items_json, add_modifier_order_item, add_ref_date_activity, add_remarks_to_settlement, add_reset_counter, add_x_count, drop_audit_logs, remove_xreading_ngrt, rename_remarks_to_manual_si
Create Date: 2026-04-16 15:01:42.366125

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bdd39396095e'
down_revision = ('c3d4e5f6g7h8', 'cashier_shift_001', 'discount_breakdown', 'add_gift_check_cheque_numbers', 'add_items_json', 'add_modifier_order_item', 'add_ref_date_activity', 'add_remarks_to_settlement', 'add_reset_counter', 'add_x_count', 'drop_audit_logs', 'remove_xreading_ngrt', 'rename_remarks_to_manual_si')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
