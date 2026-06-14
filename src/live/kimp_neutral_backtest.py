"""김프 델타-중립 북의 백테스트 엔진 (순수 로직).

``kimp_neutral`` 의 사이징/리밸런스/PnL 로직을 시계열에 적용해, rolling z-score
시그널로 북을 키우고 줄이는 Long-Kimp 중립 전략을 시뮬레이션한다. 네트워크/DB
의존이 없어 단위테스트로 결정적 검증이 가능하다.

회계 방식 — Mark-to-Market
--------------------------
매 바 ``t`` 에서:

1. 직전 보유 북(q_u, q_b)을 ``[t-1, t]`` 구간 가격변동으로 평가손익 반영::

       dMTM = q_u·(S_u[t]-S_u[t-1]) + q_b·(S_b[t-1]-S_b[t])·e[t]

   (롱은 오를 때, 숏은 내릴 때 이익. 환율은 현재값 ``e[t]`` 로 환산)
2. rolling z-score 로 목표 북 크기를 정하고(:func:`target_book_krw`),
   현재→목표로 **대칭 리밸런스**(:func:`plan_rebalance`).
3. 거래 명목에 테이커 수수료를 부과(에쿼티 차감).

에쿼티 = ``gross_cap_krw`` (자본 베이스) + 누적 PnL. 수익률은 ``gross_cap_krw``
(업비트 롱 명목 상한) 대비로 보고한다.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from live.kimp_neutral import (
    HedgeMode,
    KimpQuote,
    LotPair,
    SignalConfig,
    SizingConfig,
    plan_rebalance,
    target_book_krw,
)

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

    역김프 회귀+펀딩 수익을 보상하고 낙폭/무진입을 벌점한다. 구성::

        score = total_return_pct / (1 + mdd_pct) + 0.25·sharpe

    - ``total_return_pct`` 는 펀딩 수익을 포함한 순손익(자본 대비).
    - 큰 ``max_drawdown_pct`` 는 분모로 보상을 깎는다(위험조정).
    - ``sharpe`` 는 변동성 대비 일관성에 소폭 가점.
    - 진입이 거의 없으면(거래 0) 수익도 0이라 자연히 하위로 밀린다.
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
    """백테스트 파라미터. 시그널 + 사이징 + z 윈도우."""

    gross_cap_krw: float = 10_000_000.0
    full_build_z: float = -2.0
    flat_z: float = 0.5
    hedge_mode: HedgeMode = HedgeMode.QUANTITY
    leverage: float = 1.0
    z_window: int = 1440  # rolling z-score 표본 수
    upbit_taker_fee: float = 0.0005
    binance_taker_fee: float = 0.0005

    def signal(self) -> SignalConfig:
        return SignalConfig(
            gross_cap_krw=self.gross_cap_krw,
            full_build_z=self.full_build_z,
            flat_z=self.flat_z,
        )

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
    funding_income_krw: float
    max_drawdown_pct: float
    sharpe: float
    n_rebalances: int
    fee_drag_krw: float
    avg_kimp_pct: float
    time_in_market_pct: float
    final_kimp_pct: float


@dataclass
class KimpBacktestResult:
    metrics: BacktestMetrics
    equity: list[EquityPoint] = field(default_factory=list)


def _rolling_z(window: deque[float], value: float) -> float | None:
    """윈도우(현재값 포함 직전 표본들)에 대한 z-score. 표본 부족/무분산 시 None."""
    n = len(window)
    if n < 2:
        return None
    mu = sum(window) / n
    var = sum((x - mu) ** 2 for x in window) / n
    if var <= 0:
        return None
    return (value - mu) / math.sqrt(var)


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


@dataclass(frozen=True)
class _StrategyCtx:
    """런 전체에서 불변인 시그널/사이징/틱 제약 묶음."""

    signal: SignalConfig
    sizing: SizingConfig
    lots: LotPair


@dataclass
class _RunState:
    """백테스트 루프의 가변 누적 상태."""

    q_u: float = 0.0  # 업비트 롱 수량
    q_b: float = 0.0  # 바이낸스 숏 수량
    cum_pnl: float = 0.0
    fee_total: float = 0.0
    funding_income: float = 0.0
    n_rebalances: int = 0
    bars_in_market: int = 0
    kimp_sum: float = 0.0
    equity_series: list[float] = field(default_factory=list)


def _apply_signal(state: _RunState, bar: KimpBar, z: float, ctx: _StrategyCtx) -> None:
    """z 시그널로 목표 북을 정하고 대칭 리밸런스를 적용한다(수수료 차감 포함)."""
    sizing = ctx.sizing
    target = target_book_krw(z, ctx.signal)
    has_book = state.q_u > 0.0 or state.q_b > 0.0

    if target <= 0.0 and has_book:
        # 완전 청산: 두 레그를 0으로. 닫는 명목에 수수료 부과.
        fee = (
            state.q_u * bar.upbit_krw * sizing.upbit_taker_fee
            + state.q_b * bar.binance_usdt * bar.usd_krw * sizing.binance_taker_fee
        )
        state.cum_pnl -= fee
        state.fee_total += fee
        state.n_rebalances += 1
        state.q_u = 0.0
        state.q_b = 0.0
        return

    current_notional = state.q_u * bar.upbit_krw
    order = plan_rebalance(current_notional, target, bar.quote(), ctx.lots, sizing)
    if order.upbit_qty <= 0.0:
        return

    fee = (
        order.upbit_qty * bar.upbit_krw * sizing.upbit_taker_fee
        + order.binance_qty * bar.binance_usdt * bar.usd_krw * sizing.binance_taker_fee
    )
    state.cum_pnl -= fee
    state.fee_total += fee
    state.n_rebalances += 1
    if order.upbit_side == "BUY":
        state.q_u += order.upbit_qty
        state.q_b += order.binance_qty
    else:  # SELL → 축소
        state.q_u = max(0.0, state.q_u - order.upbit_qty)
        state.q_b = max(0.0, state.q_b - order.binance_qty)


def run_kimp_backtest(bars: list[KimpBar], config: BacktestConfig) -> KimpBacktestResult:
    """김프 중립 전략을 ``bars`` 시계열에 적용해 결과를 반환한다.

    빈/단일 바는 거래 없이 베이스라인 결과를 돌려준다.
    """
    ctx = _StrategyCtx(signal=config.signal(), sizing=config.sizing(), lots=LotPair())
    capital_base = config.gross_cap_krw

    state = _RunState()
    window: deque[float] = deque(maxlen=config.z_window)
    equity_points: list[EquityPoint] = []

    prev: KimpBar | None = None
    for bar in bars:
        k = bar.kimp
        state.kimp_sum += k

        # 1) 직전 북의 MTM 손익 반영
        if prev is not None and (state.q_u != 0.0 or state.q_b != 0.0):
            d_long = state.q_u * (bar.upbit_krw - prev.upbit_krw)
            d_short = state.q_b * (prev.binance_usdt - bar.binance_usdt) * bar.usd_krw
            state.cum_pnl += d_long + d_short

        # 1.5) 펀딩 정산: 정산 바에서, 직전부터 보유 중이던 숏 수량 기준.
        #     펀딩비 양수 → 숏 수취(롱→숏 지급)이므로 +q_b·S_b·e·rate.
        if bar.funding_rate is not None and state.q_b != 0.0:
            funding = state.q_b * bar.binance_usdt * bar.usd_krw * bar.funding_rate
            state.cum_pnl += funding
            state.funding_income += funding

        # 2) z-score (현재값 포함 윈도우 기준) → 목표 북 → 대칭 리밸런스
        window.append(k)
        z = _rolling_z(window, k)
        if z is not None:
            _apply_signal(state, bar, z, ctx)

        if state.q_u > 0.0 or state.q_b > 0.0:
            state.bars_in_market += 1

        equity = capital_base + state.cum_pnl
        state.equity_series.append(equity)
        equity_points.append(
            EquityPoint(
                ts_ms=bar.ts_ms,
                equity_krw=equity,
                kimp=k,
                zscore=z,
                notional_krw=state.q_u * bar.upbit_krw,
            )
        )
        prev = bar

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
            funding_income_krw=0.0,
            max_drawdown_pct=0.0,
            sharpe=0.0,
            n_rebalances=0,
            fee_drag_krw=0.0,
            avg_kimp_pct=0.0,
            time_in_market_pct=0.0,
            final_kimp_pct=0.0,
        )

    equity_series = state.equity_series

    # Max drawdown (에쿼티 곡선).
    peak = equity_series[0]
    max_dd = 0.0
    for v in equity_series:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)

    sharpe = _annualized_sharpe(bars, equity_series)

    return BacktestMetrics(
        n_bars=n,
        total_return_pct=(state.cum_pnl / capital_base * 100.0) if capital_base > 0 else 0.0,
        net_profit_krw=state.cum_pnl,
        funding_income_krw=state.funding_income,
        max_drawdown_pct=max_dd * 100.0,
        sharpe=sharpe,
        n_rebalances=state.n_rebalances,
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
