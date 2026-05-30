"""add upbit keys and bridge_transfers table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("upbit_api_key_enc", sa.Text(), nullable=True))
    op.add_column("user_profiles", sa.Column("upbit_api_secret_enc", sa.Text(), nullable=True))

    op.create_table(
        "bridge_transfers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("direction", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("network", sa.String(16), nullable=False, server_default="TRC20"),
        sa.Column("requested_usdt", sa.Float(), nullable=False),
        sa.Column("actual_usdt", sa.Float(), nullable=True),
        sa.Column("krw_amount", sa.Float(), nullable=True),
        sa.Column("fee_usdt", sa.Float(), nullable=True),
        sa.Column("src_order_uuid", sa.String(128), nullable=True),
        sa.Column("src_withdrawal_id", sa.String(256), nullable=True),
        sa.Column("dst_deposit_address", sa.String(256), nullable=True),
        sa.Column("dst_txid", sa.String(256), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "initiated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("bridge_transfers")
    op.drop_column("user_profiles", "upbit_api_secret_enc")
    op.drop_column("user_profiles", "upbit_api_key_enc")
