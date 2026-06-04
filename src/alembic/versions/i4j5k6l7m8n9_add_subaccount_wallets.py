"""add subaccount wallet topology tables

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-06-05

Adds the Sub-account topology for multi-strategy capital isolation:
  - wallet_accounts: master + sub bindings with per-account API keys
  - strategy_allocations: per-job capital budget (Capital Allocator state)
  - wallet_transfers: audit log of all inter-wallet transfers
  - jobs.wallet_account_id: which wallet each job trades against (nullable
    for legacy jobs created before the rollout)

Also backfills existing binance_api_credentials rows into wallet_accounts as
master records so the new client_factory can transparently locate keys.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "i4j5k6l7m8n9"
down_revision: Union[str, None] = "h3i4j5k6l7m8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # wallet_accounts
    # ------------------------------------------------------------------
    op.create_table(
        "wallet_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("env", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("sub_account_email", sa.String(320), nullable=True),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False, server_default="generic"),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("api_secret_enc", sa.Text(), nullable=True),
        sa.Column(
            "enabled_wallets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "ip_whitelist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="key_missing"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_wallet_accounts_user_id", "wallet_accounts", ["user_id"])
    op.create_index(
        "ix_wallet_user_env_role", "wallet_accounts", ["user_id", "env", "role"]
    )
    op.create_unique_constraint(
        "uq_wallet_user_env_alias", "wallet_accounts", ["user_id", "env", "alias"]
    )
    op.create_unique_constraint(
        "uq_wallet_user_env_sub_email",
        "wallet_accounts",
        ["user_id", "env", "sub_account_email"],
    )

    # ------------------------------------------------------------------
    # strategy_allocations
    # ------------------------------------------------------------------
    op.create_table(
        "strategy_allocations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "allocation_mode", sa.String(24), nullable=False, server_default="fixed_usdt"
        ),
        sa.Column("allocated_usdt", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reserved_usdt", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_strategy_allocations_job_id", "strategy_allocations", ["job_id"]
    )
    op.create_index(
        "ix_strategy_allocations_wallet_account_id",
        "strategy_allocations",
        ["wallet_account_id"],
    )
    op.create_unique_constraint(
        "uq_strategy_alloc_job", "strategy_allocations", ["job_id"]
    )

    # ------------------------------------------------------------------
    # wallet_transfers
    # ------------------------------------------------------------------
    op.create_table(
        "wallet_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
        ),
        sa.Column(
            "from_wallet_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_wallet_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_wallet_type", sa.String(24), nullable=False),
        sa.Column("to_wallet_type", sa.String(24), nullable=False),
        sa.Column("asset", sa.String(16), nullable=False, server_default="USDT"),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("binance_tran_id", sa.String(64), nullable=True),
        sa.Column("client_tran_id", sa.String(128), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_wallet_transfers_user_id", "wallet_transfers", ["user_id"])
    op.create_index(
        "ix_wallet_transfer_user_created",
        "wallet_transfers",
        ["user_id", "created_at"],
    )
    op.create_unique_constraint(
        "uq_wallet_transfer_client_tran_id", "wallet_transfers", ["client_tran_id"]
    )

    # ------------------------------------------------------------------
    # jobs.wallet_account_id
    # ------------------------------------------------------------------
    op.add_column(
        "jobs",
        sa.Column(
            "wallet_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_jobs_wallet_account_id", "jobs", ["wallet_account_id"])

    # ------------------------------------------------------------------
    # Backfill: BinanceApiCredential -> wallet_accounts (role='master')
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO wallet_accounts (
            id, user_id, env, role, sub_account_email, alias, purpose,
            api_key_enc, api_secret_enc, enabled_wallets, ip_whitelist, status,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            c.user_id,
            c.env,
            'master',
            NULL,
            'master',
            'router',
            c.api_key_enc,
            c.api_secret_enc,
            jsonb_build_object(
                'spot', true,
                'futures_um', true,
                'futures_cm', false,
                'margin', false,
                'options', false
            ),
            '[]'::jsonb,
            'active',
            c.created_at,
            c.updated_at
        FROM binance_api_credentials c
        ON CONFLICT (user_id, env, alias) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_wallet_account_id", table_name="jobs")
    op.drop_column("jobs", "wallet_account_id")

    op.drop_constraint(
        "uq_wallet_transfer_client_tran_id", "wallet_transfers", type_="unique"
    )
    op.drop_index("ix_wallet_transfer_user_created", table_name="wallet_transfers")
    op.drop_index("ix_wallet_transfers_user_id", table_name="wallet_transfers")
    op.drop_table("wallet_transfers")

    op.drop_constraint(
        "uq_strategy_alloc_job", "strategy_allocations", type_="unique"
    )
    op.drop_index(
        "ix_strategy_allocations_wallet_account_id", table_name="strategy_allocations"
    )
    op.drop_index("ix_strategy_allocations_job_id", table_name="strategy_allocations")
    op.drop_table("strategy_allocations")

    op.drop_constraint(
        "uq_wallet_user_env_sub_email", "wallet_accounts", type_="unique"
    )
    op.drop_constraint("uq_wallet_user_env_alias", "wallet_accounts", type_="unique")
    op.drop_index("ix_wallet_user_env_role", table_name="wallet_accounts")
    op.drop_index("ix_wallet_accounts_user_id", table_name="wallet_accounts")
    op.drop_table("wallet_accounts")
