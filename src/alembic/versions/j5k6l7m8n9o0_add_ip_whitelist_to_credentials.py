"""add ip_whitelist column to binance_api_credentials

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-06-07

Adds a JSONB ``ip_whitelist`` column that stores the operator's record
of which IPs they registered on Binance for the *master* API key. The
backend never enforces this — Binance does — but tracking it in DB
lets the Settings UI surface drift between intent and reality.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "j5k6l7m8n9o0"
down_revision: str | None = "i4j5k6l7m8n9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "binance_api_credentials",
        sa.Column(
            "ip_whitelist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("binance_api_credentials", "ip_whitelist")
