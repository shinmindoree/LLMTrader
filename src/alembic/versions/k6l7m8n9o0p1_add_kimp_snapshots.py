"""add kimp_snapshots table for kimchi premium time-series

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-06-09

Stores 1-minute snapshots of the kimchi premium per symbol so that:
  - the screener can show 30d mean / ±σ bands
  - the history chart can plot 1H / 1D / 7D / 30D series

Each (symbol, ts) is unique. Older rows can be pruned by a background job
once long-term storage is needed (left for a later phase).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k6l7m8n9o0p1"
down_revision: str | None = "j5k6l7m8n9o0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kimp_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upbit_krw_price", sa.Float(), nullable=False),
        sa.Column("binance_usdt_price", sa.Float(), nullable=False),
        sa.Column("usd_krw_rate", sa.Float(), nullable=False),
        sa.Column("kimp_pct", sa.Float(), nullable=False),
        sa.Column(
            "fx_source",
            sa.String(length=16),
            nullable=False,
            server_default="naver",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "ts", name="uq_kimp_symbol_ts"),
    )
    op.create_index(
        "ix_kimp_symbol_ts", "kimp_snapshots", ["symbol", "ts"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_kimp_symbol_ts", table_name="kimp_snapshots")
    op.drop_table("kimp_snapshots")
