"""김프 델타-중립 북의 단순 진입/청산 백테스트 엔진.

전략은 중간 리밸런싱을 하지 않는다. 현재 김프가 사용자가 지정한 역김프
진입 기준에 도달하고, 목표 김프까지 회귀할 때 예상 김프 손익이 왕복 수수료를
초과할 때만 업비트 현물 롱 + 바이낸스 선물 숏을 한 번 연다. 이후 목표 김프에
도달하면 전체 포지션을 한 번에 청산한다.

회계 방식 — Mark-to-Market
--------------------------
매 바 ``t`` 에서:

1. 직전 보유 북(q_u, q_b)을 ``[t-1, t]`` 구간 가격변동으로 평가손익 반영::

       dMTM = q_u·(S_u[t]-S_u[t-1]) + q_b·(S_b[t-1]-S_b[t])·e[t]

   (롱은 오를 때, 숏은 내릴 때 이익. 환율은 현재값 ``e[t]`` 로 환산)
2. 펀딩 정산 바에서는 보유 숏 명목에 펀딩비를 반영한다.
3. 포지션이 없으면 진입 조건을 평가하고, 보유 중이면 목표 김프 청산 조건만 본다.

에쿼티 = ``gross_cap_krw`` (업비트 롱 명목 상한) + 누적 PnL. 수익률은
``gross_cap_krw`` 대비로 보고한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from live.kimp_neutral import HedgeMode, KimpQuote, SizingConfig

__all__ = [
    "KimpBar",
    "BacktestConfig",
    "EquityPoint",
    "BacktestMetrics",
    "KimpBacktestResult",
    "run_kimp_backtest",
    "composite_score",
]

_MS_PER_YEAR = 365 * 24 * 3600 * 1000


def composite_score(metrics: BacktestMetrics) -> float:
    """유니버스 랭킹용 단일 점수. 높을수록 우선.

    구성::

        score = total_return_pct / (1 + mdd_pct) + 0.25·sharpe

    - ``total_return_pct`` 는 김프 손익 + 펀딩 수익 - 수수료의 순손익(자본 대비).
    - 큰 ``max_drawdown_pct`` 는 분모로 보상을 깎는다(위험조정).
    - ``sharpe`` 는 변동성 대비 일관성에 소폭 가점.
    - 진입이 없으면 수익도 0이라 자연히 하위로 밀린다.
    """
    denom = 1.0 + max(0.0, metrics.max_drawdown_pct)
    base = metrics.total_return_pct / denom
    sharpe = metrics.sharpe if math.isfinite(metrics.sharpe) else 0.0
    return base + 0.25 * sharpe


@dataclass(frozen=True)
class KimpBar:
    """한 시점의 업비트/바이낸스 가격 + 환율 + 타임스탬프.

    ``funding_rate`` 는 **이 바에서 정산되는** 바이낸스 USDT-M 무기한 펀딩비
    (소수, 예: 0.0001 = 0.01%). 정산 시점이 아닌 바는 ``None``. 숏 포지션은
    펀딩비가 양수일 때 수취(롱→숏 지급)하므로 백테스트는 ``q_b·S_b·e·rate``
    를 수익으로 가산한다.
    """

    ts_ms: int
    upbit_krw: float
    binance_usdt: float
    usd_krw: float
    funding_rate: float | None = None

    def quote(self, symbol: str = "?") -> KimpQuote:
        return KimpQuote(
            symbol=symbol,
            upbit_krw=self.upbit_krw,
            binance_usdt=self.binance_usdt,
            usd_krw=self.usd_krw,
        )

    @property
    def kimp(self) -> float:
        return self.upbit_krw / (self.binance_usdt * self.usd_krw) - 1.0


@dataclass(frozen=True)
class BacktestConfig:
    """백테스트 파라미터.

    기존 API 호환성을 위해 필드명은 ``full_build_z``/``flat_z`` 를 유지하지만,
    이 단순 백테스트에서는 각각 **진입 김프(%)** 와 **목표 청산 김프(%)** 로
    해석한다. 예: ``full_build_z=-2.0`` 은 김프 -2.0% 이하에서 진입 후보,
    ``flat_z=0.5`` 는 김프 +0.5% 이상에서 청산이다.
    """

    gross_cap_krw: float = 10_000_000.0
    full_build_z: float = -2.0
    flat_z: float = 0.5
    hedge_mode: HedgeMode = HedgeMode.QUANTITY
    leverage: float = 1.0
    z_window: int = 1440  # API 호환용. 거래 로직에는 사용하지 않는다.
    upbit_taker_fee: float = 0.0005
    binance_taker_fee: float = 0.0005

    def __post_init__(self) -> None:
        if self.gross_cap_krw <= 0:
            raise ValueError("gross_cap_krw 는 양수여야 합니다")
        if self.flat_z <= self.full_build_z:
            raise ValueError("목표 청산 김프는 진입 김프보다 커야 합니다")

    @property
    def entry_kimp(self) -> float:
        return self.full_build_z / 100.0

    @property
    def exit_kimp(self) -> float:
        return self.flat_z / 100.0

    def sizing(self) -> SizingConfig:
        return SizingConfig(
            hedge_mode=self.hedge_mode,
            leverage=self.leverage,
            upbit_taker_fee=self.upbit_taker_fee,
            binance_taker_fee=self.binance_taker_fee,
        )


@dataclass(frozen=True)
class EquityPoint:
    ts_ms: int
    equity_krw: float
    kimp: float
    zscore: float | None
    notional_krw: float


@dataclass(frozen=True)
class BacktestMetrics:
    n_bars: int
    total_return_pct: float
    net_profit_krw: float
    kimp_pnl_krw: float
    funding_income_krw: float
    funding_event_count: int
    max_drawdown_pct: float
    sharpe: float
    n_rebalances: int
    n_entries: int
    n_exits: int
    completed_trades: int
    fee_drag_krw: float
    avg_kimp_pct: float
    time_in_market_pct: float
    final_kimp_pct: float


@dataclass
class KimpBacktestResult:
    metrics: BacktestMetrics
    equity: list[EquityPoint] = field(default_factory=list)


def _median_dt_ms(bars: list[KimpBar]) -> float:
    if len(bars) < 2:
        return 0.0
    diffs = sorted(
        bars[i].ts_ms - bars[i - 1].ts_ms
        for i in range(1, len(bars))
        if bars[i].ts_ms > bars[i - 1].ts_ms
    )
    if not diffs:
        return 0.0
    mid = len(diffs) // 2
    if len(diffs) % 2 == 1:
        return float(diffs[mid])
    return (diffs[mid - 1] + diffs[mid]) / 2.0


@dataclass
class _RunState:
    """백테스트 루프의 가변 누적 상태."""

    q_u: float = 0.0  # 업비트 롱 수량
    q_b: float = 0.0  # 바이낸스 숏 수량
    cum_pnl: float = 0.0
    kimp_pnl: float = 0.0
    fee_total: float = 0.0
    funding_income: float = 0.0
    funding_event_count: int = 0
    n_entries: int = 0
    n_exits: int = 0
    bars_in_market: int = 0
    kimp_sum: float = 0.0
    equity_series: list[float] = field(default_factory=list)

    @property
    def in_market(self) -> bool:
        return self.q_u > 0.0 or self.q_b > 0.0


def _entry_quantities(
    bar: KimpBar, config: BacktestConfig, sizing: SizingConfig
) -> tuple[float, float]:
    q_u = config.gross_cap_krw / bar.upbit_krw
    q_b = q_u * sizing.short_ratio(bar.kimp)
    return q_u, q_b


def _trade_fee(q_u: float, q_b: float, bar: KimpBar, sizing: SizingConfig) -> float:
    return (
        q_u * bar.upbit_krw * sizing.upbit_taker_fee
        + q_b * bar.binance_usdt * bar.usd_krw * sizing.binance_taker_fee
    )


def _expected_round_trip_net(
    *,
    bar: KimpBar,
    q_u: float,
    q_b: float,
    exit_kimp: float,
    sizing: SizingConfig,
) -> float:
    """현재 진입 후 목표 김프에서 청산한다고 가정한 왕복 수수료 차감 기대손익."""
    base_krw = bar.binance_usdt * bar.usd_krw
    exit_upbit_krw = base_krw * (1.0 + exit_kimp)
    gross_kimp_pnl = q_u * (exit_upbit_krw - bar.upbit_krw)
    entry_fee = _trade_fee(q_u, q_b, bar, sizing)
    exit_fee = (
        q_u * exit_upbit_krw * sizing.upbit_taker_fee
        + q_b * base_krw * sizing.binance_taker_fee
    )
    return gross_kimp_pnl - entry_fee - exit_fee


def _should_enter(bar: KimpBar, config: BacktestConfig, sizing: SizingConfig) -> bool:
    if bar.kimp > config.entry_kimp:
        return False
    q_u, q_b = _entry_quantities(bar, config, sizing)
    if q_u <= 0.0 or q_b <= 0.0:
        return False
    return (
        _expected_round_trip_net(
            bar=bar,
            q_u=q_u,
            q_b=q_b,
            exit_kimp=config.exit_kimp,
            sizing=sizing,
        )
        > 0.0
    )


def _open_position(
    state: _RunState, bar: KimpBar, config: BacktestConfig, sizing: SizingConfig
) -> None:
    q_u, q_b = _entry_quantities(bar, config, sizing)
    fee = _trade_fee(q_u, q_b, bar, sizing)
    state.cum_pnl -= fee
    state.fee_total += fee
    state.q_u = q_u
    state.q_b = q_b
    state.n_entries += 1


def _close_position(state: _RunState, bar: KimpBar, sizing: SizingConfig) -> None:
    if not state.in_market:
        return
    fee = _trade_fee(state.q_u, state.q_b, bar, sizing)
    state.cum_pnl -= fee
    state.fee_total += fee
    state.q_u = 0.0
    state.q_b = 0.0
    state.n_exits += 1


def run_kimp_backtest(bars: list[KimpBar], config: BacktestConfig) -> KimpBacktestResult:
    """김프 중립 전략을 ``bars`` 시계열에 적용해 결과를 반환한다."""
    sizing = config.sizing()
    capital_base = config.gross_cap_krw

    state = _RunState()
    equity_points: list[EquityPoint] = []

    prev: KimpBar | None = None
    for bar in bars:
        k = bar.kimp
        state.kimp_sum += k

        # 1) 직전 북의 MTM 손익 반영
        if prev is not None and state.in_market:
            d_long = state.q_u * (bar.upbit_krw - prev.upbit_krw)
            d_short = state.q_b * (prev.binance_usdt - bar.binance_usdt) * bar.usd_krw
            mtm = d_long + d_short
            state.cum_pnl += mtm
            state.kimp_pnl += mtm

        # 2) 펀딩 정산: 정산 바에서, 직전부터 보유 중이던 숏 수량 기준.
        if bar.funding_rate is not None and state.in_market:
            funding = state.q_b * bar.binance_usdt * bar.usd_krw * bar.funding_rate
            state.cum_pnl += funding
            state.funding_income += funding
            state.funding_event_count += 1

        # 3) 보유 중에는 목표 김프 청산만 수행하고, 미보유 때만 신규 진입한다.
        if state.in_market:
            if k >= config.exit_kimp:
                _close_position(state, bar, sizing)
        elif _should_enter(bar, config, sizing):
            _open_position(state, bar, config, sizing)

        if state.in_market:
            state.bars_in_market += 1

        equity = capital_base + state.cum_pnl
        state.equity_series.append(equity)
        equity_points.append(
            EquityPoint(
                ts_ms=bar.ts_ms,
                equity_krw=equity,
                kimp=k,
                zscore=None,
                notional_krw=state.q_u * bar.upbit_krw,
            )
        )
        prev = bar

    # 기간 종료 시 열려 있는 포지션은 결과 확정을 위해 마지막 바 가격으로 닫는다.
    if bars and state.in_market:
        _close_position(state, bars[-1], sizing)
        final_equity = capital_base + state.cum_pnl
        state.equity_series[-1] = final_equity
        last = equity_points[-1]
        equity_points[-1] = EquityPoint(
            ts_ms=last.ts_ms,
            equity_krw=final_equity,
            kimp=last.kimp,
            zscore=None,
            notional_krw=0.0,
        )

    metrics = _compute_metrics(bars=bars, capital_base=capital_base, state=state)
    return KimpBacktestResult(metrics=metrics, equity=equity_points)


def _compute_metrics(
    *,
    bars: list[KimpBar],
    capital_base: float,
    state: _RunState,
) -> BacktestMetrics:
    n = len(bars)
    if n == 0:
        return BacktestMetrics(
            n_bars=0,
            total_return_pct=0.0,
            net_profit_krw=0.0,
            kimp_pnl_krw=0.0,
            funding_income_krw=0.0,
            funding_event_count=0,
            max_drawdown_pct=0.0,
            sharpe=0.0,
            n_rebalances=0,
            n_entries=0,
            n_exits=0,
            completed_trades=0,
            fee_drag_krw=0.0,
            avg_kimp_pct=0.0,
            time_in_market_pct=0.0,
            final_kimp_pct=0.0,
        )

    equity_series = state.equity_series

    peak = equity_series[0]
    max_dd = 0.0
    for v in equity_series:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)

    sharpe = _annualized_sharpe(bars, equity_series)
    n_actions = state.n_entries + state.n_exits

    return BacktestMetrics(
        n_bars=n,
        total_return_pct=(state.cum_pnl / capital_base * 100.0) if capital_base > 0 else 0.0,
        net_profit_krw=state.cum_pnl,
        kimp_pnl_krw=state.kimp_pnl,
        funding_income_krw=state.funding_income,
        funding_event_count=state.funding_event_count,
        max_drawdown_pct=max_dd * 100.0,
        sharpe=sharpe,
        n_rebalances=n_actions,
        n_entries=state.n_entries,
        n_exits=state.n_exits,
        completed_trades=min(state.n_entries, state.n_exits),
        fee_drag_krw=state.fee_total,
        avg_kimp_pct=(state.kimp_sum / n) * 100.0,
        time_in_market_pct=(state.bars_in_market / n) * 100.0,
        final_kimp_pct=bars[-1].kimp * 100.0,
    )


def _annualized_sharpe(bars: list[KimpBar], equity_series: list[float]) -> float:
    """per-bar 단순수익률 기반 연환산 Sharpe. 표본/분산 부족 시 0."""
    if len(equity_series) < 2:
        return 0.0
    rets: list[float] = []
    for i in range(1, len(equity_series)):
        prev_v = equity_series[i - 1]
        if prev_v != 0:
            rets.append((equity_series[i] - prev_v) / prev_v)
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / len(rets)
    sd = math.sqrt(var)
    if sd <= 0:
        return 0.0
    dt = _median_dt_ms(bars)
    bars_per_year = (_MS_PER_YEAR / dt) if dt > 0 else float(len(rets))
    return (mu / sd) * math.sqrt(bars_per_year)
