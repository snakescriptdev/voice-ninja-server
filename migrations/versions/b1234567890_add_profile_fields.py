"""Add profile fields to user model

Revision ID: b1234567890
Revises: a03a4108f1b9
Create Date: 2026-01-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1234567890'
down_revision: Union[str, Sequence[str], None] = 'a03a4108f1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add first_name, last_name, address columns to users table
    op.add_column('users', sa.Column('first_name', sa.String(), nullable=True, default=''))
    op.add_column('users', sa.Column('last_name', sa.String(), nullable=True, default=''))
    op.add_column('users', sa.Column('address', sa.String(), nullable=True, default=''))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove the added columns
    op.drop_column('users', 'address')
    op.drop_column('users', 'last_name')
    op.drop_column('users', 'first_name')