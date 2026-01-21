"""add_unified_auth_model

Revision ID: cb7c33ad6593
Revises: b1234567890
Create Date: 2026-01-19 17:15:46.937263

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb7c33ad6593'
down_revision: Union[str, Sequence[str], None] = 'b1234567890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # unified_auth table already exists in database (created by Base.metadata.create_all)
    # This migration is a placeholder to track the schema state
    pass


def downgrade() -> None:
    """Downgrade schema."""
    # No downgrade actions needed
    pass
