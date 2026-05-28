"""add auto_sweep fields to user_profiles

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("auto_sweep_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "user_profiles",
        sa.Column("auto_sweep_min_usdt", sa.Float(), nullable=False, server_default="100"),
    )
    op.add_column(
        "user_profiles",
        sa.Column("auto_sweep_buffer_usdt", sa.Float(), nullable=False, server_default="50"),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "auto_sweep_buffer_usdt")
    op.drop_column("user_profiles", "auto_sweep_min_usdt")
    op.drop_column("user_profiles", "auto_sweep_enabled")
