from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from control.enums import EventKind, JobStatus, JobType


class StrategyInfo(BaseModel):
    name: str
    path: str


class StrategyContentResponse(BaseModel):
    name: str
    path: str
    code: str


class StrategyParamsExtractRequest(BaseModel):
    code: str


class StrategyParamsExtractResponse(BaseModel):
    supported: bool
    values: dict[str, Any] = Field(default_factory=dict)
    schema_fields: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StrategyParamsApplyRequest(BaseModel):
    code: str
    param_values: dict[str, Any]


class StrategyParamsApplyResponse(BaseModel):
    code: str


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


class AdminUserItem(BaseModel):
    user_id: str
    email: str
    display_name: str
    plan: str
    email_verified: bool
    created_at: datetime


class AdminUsersResponse(BaseModel):
    users: list[AdminUserItem]
    total: int


class LlmTestRequest(BaseModel):
    input: str = "Hello"


class LlmTestResponse(BaseModel):
    output: str


class StrategyChatSessionUpsertRequest(BaseModel):
    title: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class StrategyChatSessionSummary(BaseModel):
    """Lightweight session metadata — no full data payload."""
    session_id: str
    title: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class StrategyChatSessionResponse(BaseModel):
    session_id: str
    title: str
    data: dict[str, Any] = Field(default_factory=dict)
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class StrategySyntaxError(BaseModel):
    message: str
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


class StrategySyntaxCheckRequest(BaseModel):
    code: str


class StrategySyntaxCheckResponse(BaseModel):
    valid: bool
    error: StrategySyntaxError | None = None


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


class JobCountsResponse(BaseModel):
    backtest_total: int
    live_total: int


class JobSummary(BaseModel):
    """Lightweight job representation without heavy result payload."""
    job_id: uuid.UUID
    type: JobType
    status: JobStatus
    strategy_path: str
    config: dict[str, Any]
    result_summary: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


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


class BinanceAssetBalance(BaseModel):
    asset: str
    wallet_balance: float
    available_balance: float
    unrealized_profit: float
    margin_balance: float


class BinancePositionSummary(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    position_amt: float
    entry_price: float
    break_even_price: float
    unrealized_pnl: float
    notional: float
    leverage: int
    isolated: bool


class BinanceAccountSummaryResponse(BaseModel):
    configured: bool
    connected: bool
    market: Literal["binance_futures"] = "binance_futures"
    mode: Literal["testnet", "mainnet", "custom"]
    base_url: str
    total_wallet_balance: float | None = None
    total_wallet_balance_btc: float | None = None
    total_unrealized_profit: float | None = None
    total_margin_balance: float | None = None
    available_balance: float | None = None
    can_trade: bool | None = None
    update_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    assets: list[BinanceAssetBalance] = Field(default_factory=list)
    positions: list[BinancePositionSummary] = Field(default_factory=list)
    error: str | None = None


# ── Quick Backtest ──────────────────────────────────────────


class QuickBacktestRequest(BaseModel):
    code: str
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    days: int = Field(default=30, ge=1, le=90)
    initial_balance: float = Field(default=10000.0, gt=0)
    leverage: int = Field(default=1, ge=1, le=20)
    commission: float = Field(default=0.0004, ge=0, le=0.01)
    stop_loss_pct: float = Field(default=0.05, gt=0, le=0.5)
    strategy_params: dict[str, Any] | None = None


class QuickBacktestMetrics(BaseModel):
    initial_balance: float
    final_balance: float
    total_return_pct: float
    total_pnl: float
    total_trades: int
    win_rate: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_win_pct: float
    avg_loss_pct: float
    net_profit: float
    total_commission: float


class QuickBacktestTrade(BaseModel):
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float


class QuickBacktestEquityPoint(BaseModel):
    ts: int
    balance: float


class QuickBacktestResponse(BaseModel):
    success: bool
    error_code: str | None = None
    message: str | None = None
    metrics: QuickBacktestMetrics | None = None
    trades_summary: list[QuickBacktestTrade] = Field(default_factory=list)
    equity_curve: list[QuickBacktestEquityPoint] = Field(default_factory=list)
    duration_ms: int = 0
    quota_remaining: int | None = None
    quota_reset_at: str | None = None
