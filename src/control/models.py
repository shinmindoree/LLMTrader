from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from control.enums import EventKind, JobStatus, JobType


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

    binance_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    binance_api_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    binance_base_url: Mapped[str] = mapped_column(
        String(256), nullable=False, default="https://testnet.binancefuture.com"
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
