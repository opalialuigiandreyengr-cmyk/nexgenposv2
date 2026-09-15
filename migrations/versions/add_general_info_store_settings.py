"""add general info and store settings columns to receipt_settings

Revision ID: add_gi_ss_v1
Revises: bdd39396095e
Create Date: 2026-08-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_gi_ss_v1'
down_revision = 'bdd39396095e'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated ###
    with op.batch_alter_table('receipt_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('store_name', sa.String(length=255), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('store_location', sa.String(length=500), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('contact_person', sa.String(length=255), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('contact_phone', sa.String(length=50), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('contact_email', sa.String(length=255), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('store_description', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('store_latitude', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('store_longitude', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('store_photo', sa.String(length=500), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('business_type', sa.String(length=100), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('service_types', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('payment_types', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('business_days', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('open_time', sa.String(length=5), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('close_time', sa.String(length=5), nullable=False, server_default=''))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated ###
    with op.batch_alter_table('receipt_settings', schema=None) as batch_op:
        batch_op.drop_column('close_time')
        batch_op.drop_column('open_time')
        batch_op.drop_column('business_days')
        batch_op.drop_column('payment_types')
        batch_op.drop_column('service_types')
        batch_op.drop_column('business_type')
        batch_op.drop_column('store_photo')
        batch_op.drop_column('store_longitude')
        batch_op.drop_column('store_latitude')
        batch_op.drop_column('store_description')
        batch_op.drop_column('contact_email')
        batch_op.drop_column('contact_phone')
        batch_op.drop_column('contact_person')
        batch_op.drop_column('store_location')
        batch_op.drop_column('store_name')
    # ### end Alembic commands ###