from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from control.enums import EventKind, JobStatus, JobType


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

