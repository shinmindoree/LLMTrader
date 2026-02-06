from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from control.enums import EventKind, JobStatus, JobType


class StrategyInfo(BaseModel):
    name: str
    path: str


class ChatMessage(BaseModel):
    role: str
    content: str


class StrategyGenerateRequest(BaseModel):
    user_prompt: str
    strategy_name: str | None = None
    messages: list[ChatMessage] | None = None


class StrategyIntakeRequest(BaseModel):
    user_prompt: str
    messages: list[ChatMessage] | None = None


class StrategySpec(BaseModel):
    symbol: str | None = None
    timeframe: str | None = None
    entry_logic: str | None = None
    exit_logic: str | None = None
    risk: dict[str, Any] = Field(default_factory=dict)


class StrategyIntakeResponse(BaseModel):
    intent: Literal["OUT_OF_SCOPE", "STRATEGY_CREATE", "STRATEGY_MODIFY", "STRATEGY_QA"]
    status: Literal["READY", "NEEDS_CLARIFICATION", "UNSUPPORTED_CAPABILITY", "OUT_OF_SCOPE"]
    user_message: str
    normalized_spec: StrategySpec | None = None
    missing_fields: list[str] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    development_requirements: list[str] = Field(default_factory=list)


class StrategyCapabilityResponse(BaseModel):
    supported_data_sources: list[str] = Field(default_factory=list)
    supported_indicator_scopes: list[str] = Field(default_factory=list)
    supported_context_methods: list[str] = Field(default_factory=list)
    unsupported_categories: list[str] = Field(default_factory=list)
    summary_lines: list[str] = Field(default_factory=list)


class CountItem(BaseModel):
    name: str
    count: int


class StrategyQualitySummaryResponse(BaseModel):
    window_days: int
    total_requests: int
    intake_only_requests: int
    generate_requests: int
    generation_success_count: int
    generation_failure_count: int

    ready_rate: float
    clarification_rate: float
    unsupported_rate: float
    out_of_scope_rate: float

    generation_success_rate: float
    auto_repair_rate: float
    avg_repair_attempts: float

    top_missing_fields: list[CountItem] = Field(default_factory=list)
    top_unsupported_requirements: list[CountItem] = Field(default_factory=list)
    top_error_stages: list[CountItem] = Field(default_factory=list)


class StrategyGenerateResponse(BaseModel):
    path: str | None = None
    code: str
    model_used: str | None = None
    summary: str | None = None
    backtest_ok: bool = False
    repaired: bool = False
    repair_attempts: int = 0


class StrategySaveRequest(BaseModel):
    code: str
    strategy_name: str | None = None


class StrategySaveResponse(BaseModel):
    path: str


class StrategyChatRequest(BaseModel):
    code: str
    summary: str | None = None
    messages: list[ChatMessage]


class StrategyChatResponse(BaseModel):
    content: str


class JobCreateRequest(BaseModel):
    type: JobType
    strategy_path: str
    config: dict[str, Any] = Field(default_factory=dict)


class JobPolicyCheckRequest(BaseModel):
    type: JobType
    config: dict[str, Any] = Field(default_factory=dict)


class JobPolicyCheckResponse(BaseModel):
    ok: bool
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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


class DeleteResponse(BaseModel):
    ok: bool


class DeleteAllResponse(BaseModel):
    deleted: int
    skipped_active: int


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
