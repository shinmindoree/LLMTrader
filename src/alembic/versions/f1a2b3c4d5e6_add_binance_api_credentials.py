"""add binance_api_credentials table

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-06-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "binance_api_credentials",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("user_profiles.user_id"), nullable=False),
        sa.Column("env", sa.String(32), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=False),
        sa.Column("api_secret_enc", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_binance_api_credentials_user_id", "binance_api_credentials", ["user_id"])
    op.create_unique_constraint("uq_binance_cred_user_env", "binance_api_credentials", ["user_id", "env"])

    # Migrate existing credentials
    op.execute("""
        INSERT INTO binance_api_credentials (user_id, env, api_key_enc, api_secret_enc, created_at, updated_at)
        SELECT
            user_id,
            CASE WHEN binance_base_url LIKE '%testnet%' THEN 'testnet_futures' ELSE 'mainnet' END,
            binance_api_key_enc,
            binance_api_secret_enc,
            NOW(),
            NOW()
        FROM user_profiles
        WHERE binance_api_key_enc IS NOT NULL AND binance_api_secret_enc IS NOT NULL
    """)

    # Drop old columns
    op.drop_column("user_profiles", "binance_api_key_enc")
    op.drop_column("user_profiles", "binance_api_secret_enc")
    op.drop_column("user_profiles", "binance_base_url")


def downgrade() -> None:
    op.add_column("user_profiles", sa.Column("binance_base_url", sa.String(256), nullable=False, server_default="https://testnet.binancefuture.com"))
    op.add_column("user_profiles", sa.Column("binance_api_secret_enc", sa.Text(), nullable=True))
    op.add_column("user_profiles", sa.Column("binance_api_key_enc", sa.Text(), nullable=True))

    # Restore mainnet credentials to user_profiles
    op.execute("""
        UPDATE user_profiles p
        SET binance_api_key_enc = c.api_key_enc,
            binance_api_secret_enc = c.api_secret_enc,
            binance_base_url = CASE WHEN c.env = 'mainnet' THEN 'https://fapi.binance.com' ELSE 'https://testnet.binancefuture.com' END
        FROM binance_api_credentials c
        WHERE c.user_id = p.user_id AND c.env = 'mainnet'
    """)

    op.drop_table("binance_api_credentials")
