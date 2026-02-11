"""add_user_profiles_usage_records_strategy_metadata_and_job_user_id

Revision ID: 8be195835568
Revises:
Create Date: 2026-02-11 16:42:09.945198

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "8be195835568"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- UserProfile ---
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, index=True),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("plan", sa.String(24), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(128), nullable=True, unique=True),
        sa.Column("stripe_subscription_id", sa.String(128), nullable=True),
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("binance_api_key_enc", sa.Text, nullable=True),
        sa.Column("binance_api_secret_enc", sa.Text, nullable=True),
        sa.Column(
            "binance_base_url",
            sa.String(256),
            nullable=False,
            server_default="https://testnet.binancefuture.com",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- Insert 'legacy' user profile for existing data ---
    op.execute(
        "INSERT INTO user_profiles (user_id, email) VALUES ('legacy', '') ON CONFLICT DO NOTHING"
    )

    # --- Add user_id to jobs ---
    op.add_column("jobs", sa.Column("user_id", sa.String(128), nullable=True))
    op.execute("UPDATE jobs SET user_id = 'legacy' WHERE user_id IS NULL")
    op.alter_column("jobs", "user_id", nullable=False, server_default="legacy")
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_foreign_key("fk_jobs_user_id", "jobs", "user_profiles", ["user_id"], ["user_id"])

    # --- UsageRecord ---
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("period_key", sa.String(16), nullable=False),
        sa.Column("count", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "action", "period_key", name="uq_usage_user_action_period"),
    )

    # --- StrategyMeta ---
    op.create_table(
        "strategy_metadata",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("user_profiles.user_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("strategy_name", sa.String(200), nullable=False),
        sa.Column("blob_path", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("strategy_metadata")
    op.drop_table("usage_records")
    op.drop_constraint("fk_jobs_user_id", "jobs", type_="foreignkey")
    op.drop_index("ix_jobs_user_id", table_name="jobs")
    op.drop_column("jobs", "user_id")
    op.drop_table("user_profiles")
