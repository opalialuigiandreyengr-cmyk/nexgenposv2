"""add last_login to user model

Revision ID: a1b2c3d4e5f6
Revises: bdd39396095e
Create Date: 2026-05-04 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'bdd39396095e'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated ###
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_login', sa.DateTime(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated ###
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('last_login')

    # ### end Alembic commands ###
