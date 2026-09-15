"""add table_zones and floor_boxes tables

Revision ID: add_zone_floor_boxes_v1
Revises: add_gi_ss_v1
Create Date: 2026-08-08 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_zone_floor_boxes_v1'
down_revision = 'add_gi_ss_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('table_zones',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False, server_default='floor'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_table('floor_boxes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('zone_name', sa.String(length=60), nullable=False, server_default='Main'),
        sa.Column('x_pos', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('y_pos', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('width', sa.Integer(), nullable=False, server_default='180'),
        sa.Column('height', sa.Integer(), nullable=False, server_default='120'),
        sa.PrimaryKeyConstraint('id')
    )
    # Seed the default Main floor zone so the floor plan always has a home.
    op.execute(
        "INSERT INTO table_zones (name, kind, sort_order) VALUES ('Main', 'floor', 0)"
    )


def downgrade():
    op.drop_table('floor_boxes')
    op.drop_table('table_zones')