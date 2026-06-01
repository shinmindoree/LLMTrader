"""consolidate testnet credential envs to single Demo Mode slot

Revision ID: a7c1d2e3f4b5
Revises: f1a2b3c4d5e6
Create Date: 2026-06-01

Binance Demo Trading (demo.binance.com) issues a single key pair that
authenticates both futures (testnet.binancefuture.com) and spot
(demo-api.binance.com). The previous 3-slot design
(mainnet / testnet_futures / testnet_spot) is collapsed to 2 slots
(mainnet / testnet).

- env 'testnet_futures' -> 'testnet' (the demo futures key also covers spot)
- env 'testnet_spot' rows are removed (old testnet.binance.vision system,
  incompatible with Demo Mode keys)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7c1d2e3f4b5"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove old spot-testnet rows first to avoid (user_id, env) unique
    # constraint collisions when renaming testnet_futures -> testnet.
    op.execute("DELETE FROM binance_api_credentials WHERE env = 'testnet_spot'")
    op.execute(
        "UPDATE binance_api_credentials SET env = 'testnet' WHERE env = 'testnet_futures'"
    )


def downgrade() -> None:
    # Best-effort reverse: map the consolidated 'testnet' back to
    # 'testnet_futures'. The dropped 'testnet_spot' rows cannot be restored.
    op.execute(
        "UPDATE binance_api_credentials SET env = 'testnet_futures' WHERE env = 'testnet'"
    )
