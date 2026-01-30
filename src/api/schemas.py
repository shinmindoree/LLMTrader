from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from control.enums import EventKind, JobStatus, JobType


class StrategyInfo(BaseModel):
    name: str
    path: str


class StrategyGenerateRequest(BaseModel):
    user_prompt: str
    strategy_name: str | None = None


class StrategyGenerateResponse(BaseModel):
    path: str
    code: str
    model_used: str | None = None


class JobCreateRequest(BaseModel):
    type: JobType
    strategy_path: str
    config: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    job_id: uuid.UUID
    type: JobType
    status: JobStatus
    strategy_path: str
    config: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class JobEventResponse(BaseModel):
    event_id: int
    job_id: uuid.UUID
    ts: datetime
    kind: EventKind
    level: str
    message: str
    payload: dict[str, Any] | None


class StopResponse(BaseModel):
    ok: bool


class StopAllResponse(BaseModel):
    stopped_queued: int
    stop_requested_running: int


class OrderResponse(BaseModel):
    order_id: int
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: float | None
    price: float | None
    executed_qty: float | None
    avg_price: float | None
    ts: datetime
    raw: dict[str, Any] | None


class TradeResponse(BaseModel):
    trade_id: int
    symbol: str
    order_id: int | None
    quantity: float | None
    price: float | None
    realized_pnl: float | None
    commission: float | None
    ts: datetime
    raw: dict[str, Any] | None


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    db_ok: bool
    db_error: str | None = None
