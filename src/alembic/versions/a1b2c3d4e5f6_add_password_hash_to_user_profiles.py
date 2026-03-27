"""add password_hash to user_profiles

Revision ID: a1b2c3d4e5f6
Revises: 9c0a1b2d3e4f
Create Date: 2026-03-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9c0a1b2d3e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("password_hash", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "password_hash")
