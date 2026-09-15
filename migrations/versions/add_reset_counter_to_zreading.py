"""Add reset_counter field to ZReading model

Revision ID: add_reset_counter
Revises: 
Create Date: 2025-12-18 17:00:00

This migration adds the reset_counter field to the ZReading table to support
BIR compliance requirement for tracking NGRT (Net Gross Retail Total) cycles.

When NGRT exceeds its maximum capacity (9,999,999,999.99), the reset_counter
increments by 1 and NGRT resets to continue accumulating from 0.00.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_reset_counter'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Add reset_counter column to ZReading table"""
    try:
        # Add reset_counter column with default value of 0
        op.add_column('z_reading', 
            sa.Column('reset_counter', sa.Integer(), nullable=False, server_default='0')
        )
        print("✓ Successfully added reset_counter column to ZReading table")
    except Exception as e:
        print(f"Note: Column might already exist or error occurred: {e}")
        # Continue - the column might already exist from previous attempts


def downgrade():
    """Remove reset_counter column from ZReading table"""
    try:
        op.drop_column('z_reading', 'reset_counter')
        print("✓ Successfully removed reset_counter column from ZReading table")
    except Exception as e:
        print(f"Note: Error during downgrade: {e}")
