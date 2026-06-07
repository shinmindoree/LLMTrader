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
    confirmed_plan: dict[str, Any] | None = None


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
    entry_time: datetime | None = None


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


# ── Portfolio Summary (Quant Asset Management Platform) ────

class WalletSnapshot(BaseModel):
    wallet: Literal["futures", "spot", "earn"]
    balance_usdt: float
    unrealized_pnl: float = 0.0


class AllocationSlice(BaseModel):
    category: Literal["Directional_Alpha", "Market_Neutral_Arbitrage", "Yield_Earn", "Cash"]
    allocated_usdt: float
    pct: float  # 0–100


class PortfolioSummaryResponse(BaseModel):
    total_aum_usdt: float
    total_unrealized_pnl: float
    total_realized_pnl_today: float
    wallets: list[WalletSnapshot]
    allocation: list[AllocationSlice]
    as_of: datetime


class StrategyModuleStatus(BaseModel):
    module_id: str
    name: str
    category: Literal["Directional_Alpha", "Market_Neutral_Arbitrage", "Yield_Earn"]
    enabled: bool
    allocated_usdt: float
    running_job_ids: list[str] = Field(default_factory=list)
    unrealized_pnl: float | None = None
    realized_pnl_today: float | None = None
    status: Literal["running", "idle", "error", "stopped"] = "idle"
    params: dict[str, Any] = Field(default_factory=dict)


class StrategyModuleCatalogResponse(BaseModel):
    modules: list[StrategyModuleStatus]


class AutoSweepSettingsRequest(BaseModel):
    enabled: bool
    futures_buffer_usdt: float = Field(default=200.0, ge=0)
    sweep_threshold_usdt: float = Field(default=50.0, ge=0)
    margin_restore_cap_usdt: float = Field(default=0.0, ge=0)


class AutoSweepStatusResponse(BaseModel):
    enabled: bool
    futures_buffer_usdt: float
    sweep_threshold_usdt: float
    margin_restore_cap_usdt: float
    mainnet_required: bool
    keys_configured: bool
    futures_usdt: float | None = None
    earn_usdt: float | None = None
    last_run_at: datetime | None = None
    last_action: str | None = None
    last_error: str | None = None


class WalletBalance(BaseModel):
    wallet: str
    label: str
    balance_usdt: float
    unrealized_pnl: float = 0.0
    pct: float


class WalletOverviewResponse(BaseModel):
    total_usdt: float
    wallets: list[WalletBalance]
    as_of: datetime
    error: str | None = None


# ── Live Positions Board (multi-strategy open positions) ───

class LiveStrategyPositions(BaseModel):
    job_id: str
    strategy_path: str
    strategy_name: str
    status: str
    symbols: list[str] = Field(default_factory=list)
    allocated_usdt: float = 0.0
    positions: list[BinancePositionSummary] = Field(default_factory=list)
    position_count: int = 0
    total_notional: float = 0.0
    total_unrealized_pnl: float = 0.0


class LivePositionsTotals(BaseModel):
    strategy_count: int = 0
    open_position_count: int = 0
    total_notional: float = 0.0
    total_unrealized_pnl: float = 0.0


class LivePositionsResponse(BaseModel):
    strategies: list[LiveStrategyPositions] = Field(default_factory=list)
    unattributed: list[BinancePositionSummary] = Field(default_factory=list)
    totals: LivePositionsTotals = Field(default_factory=LivePositionsTotals)
    as_of: datetime
    error: str | None = None


class BinanceCredentialStatus(BaseModel):
    env: str
    configured: bool
    api_key_masked: str | None = None
    ip_whitelist: list[str] = Field(default_factory=list)


class FundingArbitrageParams(BaseModel):
    symbol: str = "BTCUSDT"
    env: Literal["mainnet", "testnet"] = "testnet"
    allocated_usdt: float = Field(default=1000.0, gt=0, description="할당 시드 (USDT)")
    hold_days: int | None = Field(
        default=None, ge=1, le=7,
        description="목표 유지 기간(일). 설정 시 진입·청산 임계치를 AR(1) half-life 통계로 자동 계산."
    )
    entry_deadband_pct: float = Field(
        default=0.15, gt=0, le=1.0,
        description="진입 임계치 (%/정산 기준). hold_days 설정 시 자동 계산됨."
    )
    exit_deadband_pct: float = Field(
        default=0.05, gt=0, le=1.0,
        description="청산 임계치 (%/정산 기준). hold_days 설정 시 자동 계산됨."
    )
    margin_alert_ratio: float = Field(default=0.80, gt=0, lt=1.0, description="마진 비율 위험 수위 (0–1)")
    rebalance_transfer_pct: float = Field(default=0.20, gt=0, lt=1.0, description="리밸런싱 시 현물→선물 이체 비율")


class FundingScreenerItem(BaseModel):
    symbol: str
    current_rate_pct: float        # 마지막 정산 펀딩비 (%)
    annualized_pct: float          # 연환산 펀딩비 (%)
    half_life_settlements: float | None = None  # AR(1)/OU half-life (정산 횟수 단위). 통계 없으면 None
    entry_threshold_pct: float | None = None    # 최소 진입 임계치 (%/정산). half-life가 있을 때만 산출
    score: float | None = None                  # current_rate / entry_threshold (> 1 = 수익 가능)
    avg_rate_pct: float | None = None           # 과거 평균 펀딩비 (%)
    n_samples: int = 0                          # 통계 산출에 사용된 데이터 포인트 수
    quote_volume_24h: float | None = None   # 24h 현물 거래대금 (USDT)
    market_cap_usd: float | None = None     # 현물 시가총액 (USD, CoinGecko)


class FundingScreenerResponse(BaseModel):
    items: list[FundingScreenerItem]
    roundtrip_cost_pct: float      # 연산에 사용된 왕복 수수료 가정치 (%)
    error: str | None = None
    as_of: datetime


class FundingSymbolDetailPoint(BaseModel):
    t: int     # 정산 시각 (ms, UTC epoch)
    r: float   # 펀딩비 (%, percent per settlement)


class FundingWindowStat(BaseModel):
    label: str                  # "1w" / "1m" / "6m" / "1y" / "all"
    avg_pct: float | None       # 윈도우 평균 펀딩비 (%)
    annualized_pct: float | None  # 연환산 평균 (%)
    n_samples: int              # 윈도우 안에 포함된 정산 수


class FundingExtremePoint(BaseModel):
    rate_pct: float
    ts: datetime


class FundingSymbolDetailResponse(BaseModel):
    symbol: str
    as_of: datetime
    n_samples: int                          # 전체 표본 수 (계약 상장 이후 전체 기간)
    window_stats: list[FundingWindowStat]   # 1w / 1m / 6m / 1y / all
    max: FundingExtremePoint | None         # 전체 기간 내 최대 펀딩비
    min: FundingExtremePoint | None         # 전체 기간 내 최소 펀딩비
    series: list[FundingSymbolDetailPoint]  # 차트용 (전체 기간, 다운샘플)
    error: str | None = None


class FundingArbitrageStatusResponse(BaseModel):
    running: bool
    symbol: str | None = None
    spot_qty: float | None = None
    futures_short_qty: float | None = None
    current_funding_rate: float | None = None
    annualized_funding_pct: float | None = None
    next_funding_time: datetime | None = None
    unrealized_pnl: float | None = None
    accumulated_funding_income: float
    entry_fee: float | None = None  # 진입 누적 수수료(USDT)
    exit_fee: float | None = None   # 청산 누적 수수료(USDT)
    last_funding_ts: datetime | None = None
    params: FundingArbitrageParams | None = None
    last_error: str | None = None


# ── Quick Backtest ──────────────────────────────────────────


class QuickBacktestRequest(BaseModel):
    code: str
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    days: int = Field(default=30, ge=1, le=90)
    initial_balance: float = Field(default=10000.0, gt=0)
    leverage: int = Field(default=1, ge=1, le=20)
    max_position: float = Field(default=1.0, gt=0, le=1.0)
    commission: float = Field(default=0.0004, ge=0, le=0.01)
    stop_loss_pct: float = Field(default=0.05, ge=0, le=0.5)
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


# ---------------------------------------------------------------------------
# Upbit / Bridge transfer schemas
# ---------------------------------------------------------------------------


class UpbitBalanceItem(BaseModel):
    currency: str
    balance: float
    locked: float


class UpbitAccountResponse(BaseModel):
    balances: list[UpbitBalanceItem]
    krw_usdt_price: float


class BridgeOnrampRequest(BaseModel):
    usdt_amount: float = Field(gt=0)
    network: str = "TRC20"
    convert_from_krw: bool = False


class BridgeOfframpRequest(BaseModel):
    usdt_amount: float = Field(gt=0)
    network: str = "TRC20"
    sell_to_krw: bool = False
    redeem_from_earn: bool = True


class BridgeTransferResponse(BaseModel):
    id: str
    direction: str
    status: str
    network: str
    requested_usdt: float
    actual_usdt: float | None = None
    krw_amount: float | None = None
    fee_usdt: float | None = None
    src_order_uuid: str | None = None
    src_withdrawal_id: str | None = None
    dst_deposit_address: str | None = None
    dst_txid: str | None = None
    error_message: str | None = None
    initiated_at: datetime
    completed_at: datetime | None = None
    updated_at: datetime
