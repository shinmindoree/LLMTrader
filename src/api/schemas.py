from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
    wallet_account_id: uuid.UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class JobPolicyCheckRequest(BaseModel):
    type: JobType
    config: dict[str, Any] = Field(default_factory=dict)


# ── Backtest Sweep ──────────────────────────────────────────


class SweepDimensionSpec(BaseModel):
    """One swept parameter. ``mode='range'`` uses start/end/step; ``mode='values'`` uses values."""

    path: str
    mode: Literal["range", "values"]
    start: float | None = None
    end: float | None = None
    step: float | None = None
    values: list[float | str] | None = None


class SweepPreflightRequest(BaseModel):
    base_config: dict[str, Any] = Field(default_factory=dict)
    dimensions: list[SweepDimensionSpec] = Field(default_factory=list)


class SweepCreateRequest(BaseModel):
    strategy_path: str
    base_config: dict[str, Any] = Field(default_factory=dict)
    dimensions: list[SweepDimensionSpec] = Field(default_factory=list)


class SweepDimensionResolved(BaseModel):
    path: str
    values: list[float | str]


class SweepRunPreview(BaseModel):
    index: int
    params: dict[str, Any]


class SweepPreflightResponse(BaseModel):
    ok: bool
    total_runs: int
    max_runs: int
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dimensions: list[SweepDimensionResolved] = Field(default_factory=list)
    preview: list[SweepRunPreview] = Field(default_factory=list)


class SweepCreateResponse(BaseModel):
    sweep_id: str
    total_runs: int
    job_ids: list[uuid.UUID]


class SweepRunResult(BaseModel):
    job_id: uuid.UUID
    index: int
    params: dict[str, Any]
    status: JobStatus
    error: str | None = None
    result_summary: dict[str, Any] | None = None


class SweepListItem(BaseModel):
    sweep_id: str
    strategy_path: str
    symbol: str | None = None
    interval: str | None = None
    total_runs: int
    completed_runs: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    varied_paths: list[str] = Field(default_factory=list)
    created_at: datetime


class SweepDetailResponse(BaseModel):
    sweep_id: str
    strategy_path: str
    base_config: dict[str, Any] = Field(default_factory=dict)
    dimensions: list[SweepDimensionResolved] = Field(default_factory=list)
    total_runs: int
    created_at: datetime
    runs: list[SweepRunResult] = Field(default_factory=list)


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
    wallet_account_id: uuid.UUID | None = None
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
    wallet_account_id: uuid.UUID | None = None
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


class ManualLiveOrderRequest(BaseModel):
    action: Literal["ENTER", "CLOSE"]
    symbol: str
    side: Literal["LONG", "SHORT"] | None = None
    quantity: float | None = Field(default=None, gt=0)
    notional_usdt: float | None = Field(default=None, gt=0)
    use_max: bool = False


class ManualLiveOrderSizingResponse(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    mark_price: float
    leverage: float
    max_position: float
    account_equity: float
    available_balance: float
    current_position_qty: float
    current_position_notional: float
    max_notional_usdt: float
    max_quantity: float
    min_notional_usdt: float | None = None
    min_quantity: float | None = None
    max_exchange_quantity: float | None = None
    step_size: float | None = None


class ManualLiveOrderResponse(BaseModel):
    ok: bool
    action: Literal["ENTER", "CLOSE"]
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    notional_usdt: float | None = None
    mark_price: float | None = None
    reduce_only: bool
    order: dict[str, Any]


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
    update_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
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
        default=None,
        ge=1,
        le=7,
        description="목표 유지 기간(일). 설정 시 진입·청산 임계치를 AR(1) half-life 통계로 자동 계산.",
    )
    entry_deadband_pct: float = Field(
        default=0.15,
        gt=0,
        le=1.0,
        description="진입 임계치 (%/정산 기준). hold_days 설정 시 자동 계산됨.",
    )
    exit_deadband_pct: float = Field(
        default=0.05,
        gt=0,
        le=1.0,
        description="청산 임계치 (%/정산 기준). hold_days 설정 시 자동 계산됨.",
    )
    margin_alert_ratio: float = Field(
        default=0.80, gt=0, lt=1.0, description="마진 비율 위험 수위 (0–1)"
    )
    rebalance_transfer_pct: float = Field(
        default=0.20, gt=0, lt=1.0, description="리밸런싱 시 현물→선물 이체 비율"
    )


class FundingScreenerItem(BaseModel):
    symbol: str
    current_rate_pct: float  # 마지막 정산 펀딩비 (%)
    annualized_pct: float  # 연환산 펀딩비 (%)
    half_life_settlements: float | None = (
        None  # AR(1)/OU half-life (정산 횟수 단위). 통계 없으면 None
    )
    entry_threshold_pct: float | None = (
        None  # 최소 진입 임계치 (%/정산). half-life가 있을 때만 산출
    )
    score: float | None = None  # current_rate / entry_threshold (> 1 = 수익 가능)
    avg_rate_pct: float | None = None  # 과거 평균 펀딩비 (%)
    n_samples: int = 0  # 통계 산출에 사용된 데이터 포인트 수
    quote_volume_24h: float | None = None  # 24h 현물 거래대금 (USDT)
    market_cap_usd: float | None = None  # 현물 시가총액 (USD, CoinGecko)


class FundingScreenerResponse(BaseModel):
    items: list[FundingScreenerItem]
    roundtrip_cost_pct: float  # 연산에 사용된 왕복 수수료 가정치 (%)
    error: str | None = None
    as_of: datetime


class FundingSymbolDetailPoint(BaseModel):
    t: int  # 정산 시각 (ms, UTC epoch)
    r: float  # 펀딩비 (%, percent per settlement)


class FundingWindowStat(BaseModel):
    label: str  # "1w" / "1m" / "6m" / "1y" / "all"
    avg_pct: float | None  # 윈도우 평균 펀딩비 (%)
    annualized_pct: float | None  # 연환산 평균 (%)
    n_samples: int  # 윈도우 안에 포함된 정산 수


class FundingExtremePoint(BaseModel):
    rate_pct: float
    ts: datetime


class FundingSymbolDetailResponse(BaseModel):
    symbol: str
    as_of: datetime
    n_samples: int  # 전체 표본 수 (계약 상장 이후 전체 기간)
    window_stats: list[FundingWindowStat]  # 1w / 1m / 6m / 1y / all
    max: FundingExtremePoint | None  # 전체 기간 내 최대 펀딩비
    min: FundingExtremePoint | None  # 전체 기간 내 최소 펀딩비
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
    exit_fee: float | None = None  # 청산 누적 수수료(USDT)
    last_funding_ts: datetime | None = None
    params: FundingArbitrageParams | None = None
    last_error: str | None = None


# ── Kimchi Premium (김프) Arbitrage ─────────────────────────


class KimpFxRateResponse(BaseModel):
    pair: Literal["USDT/KRW", "USD/KRW"] = "USDT/KRW"
    rate: float
    source: str
    fetched_at: datetime
    stale: bool


class KimpScreenerItem(BaseModel):
    symbol: str  # 예: "BTC"
    upbit_krw_price: float
    binance_usdt_price: float  # Binance USDT-M 무기한 마크가격
    binance_spot_price: float | None = None  # Binance 현물 가격(USDT/coin)
    usdt_krw_rate: float
    usd_krw_rate: float | None = None
    kimp_pct: float  # 0.0345 == 3.45% (선물 마크 기준, USDT 환율)
    bank_kimp_pct: float | None = None  # 선물 마크 기준, 은행 환율
    spot_kimp_pct: float | None = None  # 현물 기준, USDT 환율
    spot_bank_kimp_pct: float | None = None  # 현물 기준, 은행 환율
    mean_30d_pct: float | None = None
    std_30d_pct: float | None = None
    zscore_30d: float | None = None  # (kimp - mean) / std
    n_samples_30d: int = 0
    funding_rate_pct: float | None = None  # 직전 펀딩비(%) (예: 0.01 == 0.01%)
    funding_interval_hours: float | None = None
    next_funding_time: datetime | None = None
    upbit_quote_volume_krw: float | None = None  # Upbit 24h 누적 거래대금(KRW)
    signal: Literal["entry", "exit", "hold"] = "hold"


class KimpScreenerResponse(BaseModel):
    items: list[KimpScreenerItem]
    fx: KimpFxRateResponse
    bank_fx: KimpFxRateResponse | None = None
    errors: list[str] = Field(default_factory=list)
    as_of: datetime


class KimpHistoryPoint(BaseModel):
    t: int  # epoch ms (UTC)
    p: float  # kimp 비율 (예: 0.0345)


class KimpFundingPoint(BaseModel):
    t: int  # epoch ms (UTC) — 펀딩 정산 시각
    r: float  # 펀딩비 (퍼센트 단위, 예: 0.0100 == 0.0100%)


class KimpHistoryResponse(BaseModel):
    symbol: str
    range: Literal["1H", "1D", "7D", "30D", "ALL"]
    rate_mode: Literal["usdt", "bank"] = "usdt"
    as_of: datetime
    mean_pct: float | None
    std_pct: float | None
    n_samples: int
    series: list[KimpHistoryPoint]
    # 김프 트렌드와 함께 보여줄 바이낸스 무기한 펀딩비 시계열(좌측 Y축 오버레이용).
    funding_series: list[KimpFundingPoint] = []


# ── Kimchi Premium Delta-Neutral Arbitrage (실거래/백테스트) ──


class KimpArbitrageParams(BaseModel):
    """김프 델타-중립 봇 파라미터.

    중립북 = 업비트 현물 롱 + 바이낸스 무기한 숏. 김프가 쌀 때(z 낮음) 북을 키우고
    비쌀 때(z 높음) 줄인다. 두 레그는 항상 대칭 이동한다.
    """

    symbol: str = "BTC"
    env: Literal["mainnet", "testnet"] = "testnet"
    mode: Literal["live", "paper"] = Field(
        default="live",
        description="live=실주문, paper=모의체결(주문 없이 시세로 시뮬, API 키 불필요)",
    )
    gross_cap_krw: float = Field(
        default=10_000_000.0, gt=0, description="최대 북 명목(업비트 롱 기준, KRW)"
    )
    full_build_z: float = Field(default=-2.0, description="이 z 이하로 김프가 싸지면 풀사이즈 진입")
    flat_z: float = Field(default=0.5, description="이 z 이상으로 김프가 비싸지면 청산")
    hedge_mode: Literal["quantity", "delta"] = Field(
        default="quantity",
        description="quantity=수량일치(작은 누수), delta=공통변동 델타 0",
    )
    leverage: float = Field(default=1.0, ge=1.0, le=10.0, description="바이낸스 숏 레버리지")
    z_window_days: int = Field(default=30, ge=1, le=90, description="z-score 통계 윈도우(일)")
    upbit_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)
    binance_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)
    margin_alert_ratio: float = Field(
        default=0.80, gt=0, lt=1.0, description="바이낸스 마진 비율 위험 수위(이상이면 북 축소)"
    )

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.flat_z <= self.full_build_z:
            raise ValueError("flat_z 는 full_build_z 보다 커야 합니다")


class KimpArbitrageStatusResponse(BaseModel):
    running: bool
    mode: Literal["live", "paper"] = "live"
    symbol: str | None = None
    upbit_long_qty: float | None = None
    binance_short_qty: float | None = None
    kimp_pct: float | None = None  # 현재 김프 (0.0345 == 3.45%)
    zscore: float | None = None  # 현재 김프 z-score
    target_notional_krw: float | None = None  # 시그널이 요구하는 목표 북(KRW)
    current_notional_krw: float | None = None  # 실제 보유 북(KRW)
    fx_hedge_usd: float | None = None  # 권장 USD 매도 헤지량
    coin_delta_qty: float | None = None  # 순 코인 델타(롱-숏)
    price_delta_krw: float | None = None  # 공통 가격변동 방향성 델타(KRW)
    unrealized_pnl_krw: float | None = None
    accumulated_fee_krw: float = 0.0
    binance_margin_ratio: float | None = None
    last_rebalance_ts: datetime | None = None
    params: KimpArbitrageParams | None = None
    last_error: str | None = None


class KimpBacktestRequest(BaseModel):
    symbol: str = "BTC"
    days: int = Field(default=30, ge=1, le=365, description="조회 기간(일)")
    price_source: Literal["candles", "snapshots"] = Field(
        default="candles",
        description="candles=업비트/바이낸스 선물 캔들(정확), snapshots=저장된 kimp_snapshots",
    )
    rate_mode: Literal["usdt", "bank"] = Field(
        default="usdt", description="KRW 환산 기준(candles 모드 전용)"
    )
    include_funding: bool = Field(
        default=True, description="숏 펀딩비 수익 반영(candles 모드 전용)"
    )
    gross_cap_krw: float = Field(default=10_000_000.0, gt=0)
    full_build_z: float = Field(
        default=-2.0, description="진입 김프 기준(%). 음수면 역김프 구간"
    )
    flat_z: float = Field(default=0.5, description="목표 청산 김프(%). 예: 0.5 = +0.5%")
    hedge_mode: Literal["quantity", "delta"] = "quantity"
    leverage: float = Field(default=1.0, ge=1.0, le=10.0)
    z_window_points: int = Field(
        default=1440, ge=10, le=43200, description="하위 호환용. 현 백테스트 거래 로직에는 미사용"
    )
    upbit_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)
    binance_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)


class KimpBacktestMetrics(BaseModel):
    n_bars: int
    total_return_pct: float  # 투입자본 대비 (gross_cap 기준)
    net_profit_krw: float
    kimp_pnl_krw: float = 0.0  # 김프/스프레드 MTM 손익(수수료·펀딩 제외)
    funding_income_krw: float  # 누적 숏 펀딩 수익(스프레드 손익과 분리)
    funding_event_count: int = 0
    max_drawdown_pct: float
    sharpe: float
    n_rebalances: int  # 하위 호환용: 진입+청산 액션 수
    n_entries: int = 0
    n_exits: int = 0
    completed_trades: int = 0
    fee_drag_krw: float
    avg_kimp_pct: float
    time_in_market_pct: float
    final_kimp_pct: float


class KimpBacktestEquityPoint(BaseModel):
    t: int  # epoch ms (UTC)
    equity_krw: float
    kimp_pct: float
    zscore: float | None = None
    notional_krw: float


class KimpBacktestTrade(BaseModel):
    """완결된 한 사이클의 진입/청산 기록(백테스트-실거래 일치성 검증용)."""

    index: int
    entry_t: int  # 진입 epoch ms (UTC)
    exit_t: int  # 청산 epoch ms (UTC)
    entry_kimp_pct: float
    exit_kimp_pct: float
    entry_upbit_krw: float
    exit_upbit_krw: float
    qty_upbit: float
    qty_binance: float
    notional_krw: float
    kimp_pnl_krw: float
    funding_income_krw: float
    funding_events: int
    fee_krw: float
    net_pnl_krw: float
    return_pct: float
    holding_bars: int
    exit_reason: str  # "target" | "period_end"


class KimpBacktestResponse(BaseModel):
    success: bool
    error: str | None = None
    symbol: str
    as_of: datetime
    metrics: KimpBacktestMetrics | None = None
    equity_curve: list[KimpBacktestEquityPoint] = Field(default_factory=list)
    trades: list[KimpBacktestTrade] = Field(default_factory=list)


class KimpUniverseBacktestRequest(BaseModel):
    """유니버스 일괄 백테스트 요청. 종목 미지정 시 스크리너 유니버스 전체."""

    symbols: list[str] | None = Field(
        default=None, description="대상 베이스 심볼 목록(미지정 시 유니버스 전체)"
    )
    limit: int = Field(default=30, ge=1, le=200, description="유니버스 상위 N개로 제한")
    days: int = Field(default=30, ge=1, le=365)
    rate_mode: Literal["usdt", "bank"] = "usdt"
    include_funding: bool = True
    gross_cap_krw: float = Field(default=10_000_000.0, gt=0)
    full_build_z: float = -2.0
    flat_z: float = 0.5
    hedge_mode: Literal["quantity", "delta"] = "quantity"
    leverage: float = Field(default=1.0, ge=1.0, le=10.0)
    z_window_points: int = Field(default=720, ge=10, le=43200)
    upbit_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)
    binance_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)
    concurrency: int = Field(default=4, ge=1, le=8, description="동시 데이터 적재 수")


class KimpUniverseBacktestItem(BaseModel):
    symbol: str
    score: float | None = None  # composite_score (None=실패)
    error: str | None = None
    n_bars: int = 0
    n_funding_events: int = 0
    metrics: KimpBacktestMetrics | None = None


class KimpUniverseBacktestResponse(BaseModel):
    success: bool
    error: str | None = None
    as_of: datetime
    n_symbols: int = 0
    n_ok: int = 0
    items: list[KimpUniverseBacktestItem] = Field(default_factory=list)


# ── Kimp Paper Auto-Pilot Portfolio (랭킹 상위 N 자동 페이퍼 운용) ──


class KimpPaperPortfolioParams(BaseModel):
    """페이퍼 자동운용 파라미터.

    유니버스를 백테스트로 랭킹해 상위 ``top_n`` 종목을 각각 ``capital_per_slot_krw``
    규모의 **페이퍼 중립북**으로 운용한다. ``rerank_hours`` 마다 재랭킹해 슬롯을
    교체한다. 실주문/키가 없으므로 안전하게 다종목 전략을 검증한다.
    """

    top_n: int = Field(default=3, ge=1, le=10, description="동시 운용 슬롯 수")
    capital_per_slot_krw: float = Field(
        default=10_000_000.0, gt=0, description="슬롯당 최대 북(KRW)"
    )
    candidate_limit: int = Field(
        default=30, ge=1, le=200, description="재랭킹 시 평가할 유니버스 상위 N"
    )
    rerank_hours: float = Field(
        default=6.0, ge=0.5, le=168.0, description="재랭킹 주기(시간)"
    )
    rank_days: int = Field(default=30, ge=1, le=365, description="랭킹 백테스트 기간(일)")
    rank_z_window_points: int = Field(default=720, ge=10, le=43200)
    full_build_z: float = -2.0
    flat_z: float = 0.5
    hedge_mode: Literal["quantity", "delta"] = "quantity"
    leverage: float = Field(default=1.0, ge=1.0, le=10.0)
    z_window_days: int = Field(default=30, ge=1, le=90, description="페이퍼 틱 z 윈도우(일)")
    upbit_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)
    binance_taker_fee: float = Field(default=0.0005, ge=0, le=0.01)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.flat_z <= self.full_build_z:
            raise ValueError("flat_z 는 full_build_z 보다 커야 합니다")


class KimpPaperSlotStatus(BaseModel):
    symbol: str
    score: float | None = None  # 마지막 랭킹 점수
    kimp_pct: float | None = None
    zscore: float | None = None
    target_notional_krw: float | None = None
    current_notional_krw: float | None = None
    upbit_long_qty: float | None = None
    binance_short_qty: float | None = None
    unrealized_pnl_krw: float | None = None
    accumulated_fee_krw: float = 0.0
    last_rebalance_ts: datetime | None = None
    last_error: str | None = None


class KimpPaperPortfolioStatus(BaseModel):
    running: bool
    top_n: int = 0
    capital_per_slot_krw: float = 0.0
    n_slots: int = 0
    total_notional_krw: float = 0.0
    total_unrealized_pnl_krw: float = 0.0
    total_fee_krw: float = 0.0
    rerank_hours: float = 0.0
    last_rank_ts: datetime | None = None
    next_rank_ts: datetime | None = None
    slots: list[KimpPaperSlotStatus] = Field(default_factory=list)
    params: KimpPaperPortfolioParams | None = None
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
