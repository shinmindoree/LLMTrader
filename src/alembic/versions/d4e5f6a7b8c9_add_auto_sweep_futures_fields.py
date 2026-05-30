"""add auto_sweep futures buffer fields

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column(
            "auto_sweep_futures_buffer_usdt",
            sa.Float(),
            nullable=False,
            server_default="200",
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "auto_sweep_sweep_threshold_usdt",
            sa.Float(),
            nullable=False,
            server_default="50",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "auto_sweep_sweep_threshold_usdt")
    op.drop_column("user_profiles", "auto_sweep_futures_buffer_usdt")
