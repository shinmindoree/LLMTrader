from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from control.enums import EventKind, JobStatus, JobType


class BridgeDirection(str, enum.Enum):
    UPBIT_TO_BINANCE = "UPBIT_TO_BINANCE"
    BINANCE_TO_UPBIT = "BINANCE_TO_UPBIT"


class BridgeStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONVERTING = "CONVERTING"
    WITHDRAWING = "WITHDRAWING"
    CONFIRMING = "CONFIRMING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Base(DeclarativeBase):
    pass


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    plan: Mapped[str] = mapped_column(String(24), nullable=False, default="free")

    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    email_verification_token: Mapped[str | None] = mapped_column(String(128), nullable=True)

    upbit_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    upbit_api_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    auto_sweep_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    auto_sweep_min_usdt: Mapped[float] = mapped_column(
        Float, nullable=False, default=100.0, server_default="100"
    )
    auto_sweep_buffer_usdt: Mapped[float] = mapped_column(
        Float, nullable=False, default=50.0, server_default="50"
    )
    auto_sweep_futures_buffer_usdt: Mapped[float] = mapped_column(
        Float, nullable=False, default=200.0, server_default="200"
    )
    auto_sweep_sweep_threshold_usdt: Mapped[float] = mapped_column(
        Float, nullable=False, default=50.0, server_default="50"
    )
    # Cap (USDT) on how much is pulled from Simple Earn into the Futures
    # wallet right before a live entry (JIT margin restore). 0 = unlimited
    # (restore the entire Earn position, legacy behaviour).
    auto_sweep_margin_restore_usdt: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_profiles.user_id"), nullable=False, index=True, default="legacy"
    )
    type: Mapped[JobType] = mapped_column(String(16), nullable=False)
    status: Mapped[JobStatus] = mapped_column(String(24), nullable=False, default=JobStatus.PENDING)

    strategy_path: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Sub-account topology: which wallet (master or sub) this job trades against.
    # Nullable for backward compatibility with legacy jobs created before the
    # topology rollout; new jobs should always set this.
    wallet_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallet_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    kind: Mapped[EventKind] = mapped_column(String(24), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("job_id", "order_id", name="uq_orders_job_order_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id", ondelete="CASCADE"), index=True
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    order_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="")

    quantity: Mapped[float | None] = mapped_column(nullable=True)
    price: Mapped[float | None] = mapped_column(nullable=True)
    executed_qty: Mapped[float | None] = mapped_column(nullable=True)
    avg_price: Mapped[float | None] = mapped_column(nullable=True)

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("job_id", "trade_id", name="uq_trades_job_trade_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.job_id", ondelete="CASCADE"), index=True
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    trade_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    quantity: Mapped[float | None] = mapped_column(nullable=True)
    price: Mapped[float | None] = mapped_column(nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(nullable=True)
    commission: Mapped[float | None] = mapped_column(nullable=True)

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class StrategyChatSession(Base):
    __tablename__ = "strategy_chat_sessions"
    __table_args__ = (UniqueConstraint("user_id", "session_id", name="uq_strategy_chat_sessions_user_session"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True, default="default")
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New chat")
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        index=True,
    )


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyQualityLog(Base):
    __tablename__ = "strategy_quality_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    endpoint: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    user_prompt_len: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    unsupported_requirements: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    development_requirements: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    generation_attempted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    generation_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    repaired: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    repair_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("user_id", "action", "period_key", name="uq_usage_user_action_period"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_profiles.user_id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyMeta(Base):
    __tablename__ = "strategy_metadata"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_profiles.user_id"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(200), nullable=False)
    blob_path: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BinanceApiCredential(Base):
    __tablename__ = "binance_api_credentials"
    __table_args__ = (UniqueConstraint("user_id", "env", name="uq_binance_cred_user_env"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_profiles.user_id"), nullable=False, index=True
    )
    env: Mapped[str] = mapped_column(String(32), nullable=False)  # 'mainnet' | 'testnet' (Demo Mode)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # Operator-supplied memo of which IPs they registered on Binance for
    # this key. The backend never enforces this — Binance does — but
    # storing it lets the UI surface what the user *thinks* is whitelisted
    # so drift between their notes and reality can be spotted.
    ip_whitelist: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]", default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BridgeTransfer(Base):
    """Cross-exchange transfer record (Upbit ↔ Binance)."""

    __tablename__ = "bridge_transfers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_profiles.user_id"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=BridgeStatus.PENDING)
    network: Mapped[str] = mapped_column(String(16), nullable=False, default="TRC20")

    requested_usdt: Mapped[float] = mapped_column(Float, nullable=False)
    actual_usdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    krw_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_usdt: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Source-side IDs
    src_order_uuid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    src_withdrawal_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Destination-side IDs
    dst_deposit_address: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dst_txid: Mapped[str | None] = mapped_column(String(256), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# Sub-account topology (Phase 0)
# ---------------------------------------------------------------------------


class WalletRole(str, enum.Enum):
    MASTER = "master"
    SUB = "sub"


class WalletPurpose(str, enum.Enum):
    ROUTER = "router"                # master, owns Earn pool + routes funds
    DIRECTIONAL = "directional"      # directional alpha (futures)
    ARBITRAGE = "arbitrage"          # spot/futures arbitrage
    DERIVATIVES = "derivatives"      # futures + options paired strategies
    EARN = "earn"                    # dedicated earn-only sub (optional)
    GENERIC = "generic"


class WalletAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    KEY_MISSING = "key_missing"
    KEY_INVALID = "key_invalid"
    BINANCE_MISSING = "binance_missing"


class WalletAccount(Base):
    """Master or sub-account binding for a user.

    A user has exactly one master per env and zero or more sub-accounts.
    Each row owns its own (api_key, api_secret) — for masters this is the
    master API key, for subs this is the sub-account's own trading key
    (which retail users must create manually in the Binance web UI; the
    backend then registers IP whitelist via the master key).
    """

    __tablename__ = "wallet_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "env", "alias", name="uq_wallet_user_env_alias"),
        UniqueConstraint(
            "user_id", "env", "sub_account_email", name="uq_wallet_user_env_sub_email"
        ),
        Index("ix_wallet_user_env_role", "user_id", "env", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_profiles.user_id"), nullable=False, index=True
    )
    env: Mapped[str] = mapped_column(String(32), nullable=False)  # "mainnet" | "testnet"
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    sub_account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    alias: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(24), nullable=False, default=WalletPurpose.GENERIC
    )

    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled_wallets: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ip_whitelist: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=WalletAccountStatus.KEY_MISSING
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AllocationMode(str, enum.Enum):
    FIXED_USDT = "fixed_usdt"
    PCT_OF_WALLET = "pct_of_wallet"
    VOL_TARGET = "vol_target"


class StrategyAllocation(Base):
    """Per-job capital budget — source of truth for the Capital Allocator.

    A job has at most one active allocation. ``reserved_usdt`` is updated
    by the pre-trade gate as positions/orders are opened; ``allocated_usdt``
    is the upper bound set by the user (or the allocator policy).
    """

    __tablename__ = "strategy_allocations"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_strategy_alloc_job"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wallet_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallet_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    allocation_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AllocationMode.FIXED_USDT
    )
    allocated_usdt: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reserved_usdt: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WalletTransferStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class WalletTransfer(Base):
    """Audit log of every master↔sub / sub↔sub / wallet-type transfer.

    NULL ``from_wallet_account_id`` / ``to_wallet_account_id`` denotes the
    master account itself (when transferring between master wallets only).
    ``client_tran_id`` is our idempotency key passed to Binance.
    """

    __tablename__ = "wallet_transfers"
    __table_args__ = (
        UniqueConstraint("client_tran_id", name="uq_wallet_transfer_client_tran_id"),
        Index("ix_wallet_transfer_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("user_profiles.user_id"), nullable=False, index=True
    )
    from_wallet_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallet_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    to_wallet_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallet_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_wallet_type: Mapped[str] = mapped_column(String(24), nullable=False)
    to_wallet_type: Mapped[str] = mapped_column(String(24), nullable=False)
    asset: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=WalletTransferStatus.PENDING
    )
    binance_tran_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_tran_id: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

