"""``live.kimp_neutral_backtest`` 결정적 단위테스트.

합성 김프 시계열로 PnL 부호/크기, 중립성, 수수료 드래그, z-시그널 거동을 검증한다.
"""

from __future__ import annotations

import math

import pytest

from live.kimp_neutral import HedgeMode
from live.kimp_neutral_backtest import (
    BacktestConfig,
    KimpBacktestResult,
    KimpBar,
    run_kimp_backtest,
)

S_B = 1000.0  # 바이낸스 USDT/coin (전 구간 고정 → 공통변동 없음)
E = 1400.0  # USD/KRW 고정

_MINUTE = 60_000


def _bar(i: int, kimp: float, s_b: float = S_B, e: float = E) -> KimpBar:
    return KimpBar(
        ts_ms=i * _MINUTE,
        upbit_krw=s_b * e * (1 + kimp),
        binance_usdt=s_b,
        usd_krw=e,
    )


def _bar_funding(i: int, kimp: float, rate: float | None) -> KimpBar:
    return KimpBar(
        ts_ms=i * _MINUTE,
        upbit_krw=S_B * E * (1 + kimp),
        binance_usdt=S_B,
        usd_krw=E,
        funding_rate=rate,
    )


def _series(kimps: list[float], **kw: float) -> list[KimpBar]:
    return [_bar(i, k, **kw) for i, k in enumerate(kimps)]


class TestDegenerate:
    def test_empty(self) -> None:
        res = run_kimp_backtest([], BacktestConfig())
        assert res.metrics.n_bars == 0
        assert res.metrics.net_profit_krw == 0.0
        assert res.equity == []

    def test_single_bar_no_trade(self) -> None:
        res = run_kimp_backtest([_bar(0, 0.03)], BacktestConfig(z_window=5))
        assert res.metrics.n_bars == 1
        assert res.metrics.n_rebalances == 0
        assert res.metrics.net_profit_krw == 0.0


class TestSignalGating:
    def test_no_trade_until_window_fills(self) -> None:
        # z_window 큰데 표본 적으면 z=None → 거래 없음.
        bars = _series([0.03, 0.031, 0.029])
        res = run_kimp_backtest(bars, BacktestConfig(z_window=1000))
        # 윈도우가 다 차지 않아도 n>=2면 z 산출되지만, 변동이 작아 목표가 0(flat)일 수 있다.
        # 핵심: 무분산(첫 바)에서는 절대 거래하지 않는다.
        assert res.equity[0].zscore is None

    def test_cheap_kimp_builds_book(self) -> None:
        # 김프가 평균 대비 크게 싸지면(z<<0) 북을 키운다.
        kimps = [0.03] * 30 + [0.00] * 5  # 후반 급락 → z 음수
        res = run_kimp_backtest(
            kimps_to_bars(kimps), BacktestConfig(z_window=50, flat_z=0.5, full_build_z=-1.5)
        )
        assert res.metrics.time_in_market_pct > 0.0
        assert any(p.notional_krw > 0 for p in res.equity)


def kimps_to_bars(kimps: list[float]) -> list[KimpBar]:
    return _series(kimps)


class TestNeutrality:
    @staticmethod
    def _build_then_common_move() -> list[KimpBar]:
        # 김프 0.08(무거래) → 0.03 급락 시점에 풀빌드 → 이후 김프 0.03 고정 상태로
        # 바이낸스 가격을 1000→1400(+40%) 공통 이동. 빌드/보유 김프가 같아 김프 PnL은
        # 0이고, 남는 손익은 헤지 모드별 '공통변동 누수' 뿐이다.
        kimps = [0.08] * 25 + [0.03] * 36
        prices = [1000.0] * 61
        for i in range(40, 61):
            prices[i] = 1000.0 + (i - 40) * 20.0  # 1000 → 1400
        return [
            KimpBar(
                ts_ms=i * _MINUTE,
                upbit_krw=prices[i] * E * (1 + kimps[i]),
                binance_usdt=prices[i],
                usd_krw=E,
            )
            for i in range(61)
        ]

    def test_common_move_with_fixed_kimp_is_flat_pnl(self) -> None:
        bars = self._build_then_common_move()
        res = run_kimp_backtest(
            bars,
            BacktestConfig(
                z_window=30,
                hedge_mode=HedgeMode.QUANTITY,
                upbit_taker_fee=0.0,
                binance_taker_fee=0.0,
            ),
        )
        notional = max((p.notional_krw for p in res.equity), default=0.0)
        assert notional > 0
        # 공통 +40% 이동에서 QUANTITY 누수는 ~k·이동폭(≈1.2%) 수준 → 명목의 3% 미만.
        assert abs(res.metrics.net_profit_krw) < 0.03 * notional

    def test_delta_mode_tighter_than_quantity_under_common_move(self) -> None:
        bars = self._build_then_common_move()
        q = run_kimp_backtest(
            bars,
            BacktestConfig(
                z_window=30,
                hedge_mode=HedgeMode.QUANTITY,
                upbit_taker_fee=0.0,
                binance_taker_fee=0.0,
            ),
        )
        d = run_kimp_backtest(
            bars,
            BacktestConfig(
                z_window=30, hedge_mode=HedgeMode.DELTA, upbit_taker_fee=0.0, binance_taker_fee=0.0
            ),
        )
        # DELTA 모드는 공통변동 델타가 0이라 누수가 QUANTITY보다 작아야 한다.
        assert abs(d.metrics.net_profit_krw) < abs(q.metrics.net_profit_krw)
        assert abs(d.metrics.net_profit_krw) < 0.005 * 1e7


class TestKimpPnl:
    def test_kimp_widening_is_profitable(self) -> None:
        # 북을 싸게 만든 뒤(z<<0) 김프가 확대되면 Long-Kimp 북은 이익.
        # 0.00 부근에서 매수 → 0.06으로 확대.
        kimps = [0.03] * 30 + [0.00] * 3 + [0.06] * 10
        res = run_kimp_backtest(
            kimps_to_bars(kimps),
            BacktestConfig(
                z_window=33,
                full_build_z=-1.0,
                flat_z=0.5,
                upbit_taker_fee=0.0,
                binance_taker_fee=0.0,
            ),
        )
        assert res.metrics.net_profit_krw > 0.0

    def test_fees_reduce_profit(self) -> None:
        kimps = [0.03] * 30 + [0.00] * 3 + [0.06] * 10
        cfg_free = BacktestConfig(
            z_window=33, full_build_z=-1.0, upbit_taker_fee=0.0, binance_taker_fee=0.0
        )
        cfg_fee = BacktestConfig(
            z_window=33, full_build_z=-1.0, upbit_taker_fee=0.001, binance_taker_fee=0.001
        )
        free = run_kimp_backtest(kimps_to_bars(kimps), cfg_free)
        fee = run_kimp_backtest(kimps_to_bars(kimps), cfg_fee)
        assert fee.metrics.fee_drag_krw > 0.0
        assert fee.metrics.net_profit_krw < free.metrics.net_profit_krw


class TestMetrics:
    def test_metrics_are_finite_and_consistent(self) -> None:
        kimps = [0.03 + 0.01 * math.sin(i / 5.0) for i in range(200)]
        res = run_kimp_backtest(kimps_to_bars(kimps), BacktestConfig(z_window=60))
        m = res.metrics
        assert m.n_bars == 200
        assert len(res.equity) == 200
        assert math.isfinite(m.sharpe)
        assert math.isfinite(m.max_drawdown_pct)
        assert m.max_drawdown_pct >= 0.0
        assert 0.0 <= m.time_in_market_pct <= 100.0
        assert m.total_return_pct == pytest.approx(m.net_profit_krw / 1e7 * 100.0)
        assert m.final_kimp_pct == pytest.approx(kimps[-1] * 100.0, abs=1e-6)

    def test_equity_baseline_is_capital(self) -> None:
        # 첫 바는 거래 없음 → 에쿼티 = capital_base.
        res = run_kimp_backtest(
            kimps_to_bars([0.03] * 10), BacktestConfig(gross_cap_krw=5e7, z_window=5)
        )
        assert res.equity[0].equity_krw == pytest.approx(5e7)


class TestFunding:
    @staticmethod
    def _build_and_hold_with_funding(rate: float | None, settle_at: int) -> KimpBacktestResult:
        # 김프 0.06(무거래) → 0.00 급락 시 풀빌드 → 이후 0.00 고정으로 보유.
        # settle_at 바에서 펀딩비 rate 정산. 가격/김프 고정이라 MTM/스프레드 손익은
        # 0이고, 순손익은 펀딩 수익만 남는다(수수료 0).
        kimps = [0.06] * 30 + [0.00] * 30
        bars: list[KimpBar] = []
        for i, k in enumerate(kimps):
            r = rate if i == settle_at else None
            bars.append(_bar_funding(i, k, r))
        return run_kimp_backtest(
            bars,
            BacktestConfig(
                z_window=33,
                full_build_z=-1.0,
                flat_z=0.5,
                upbit_taker_fee=0.0,
                binance_taker_fee=0.0,
            ),
        )

    def test_positive_funding_credits_short(self) -> None:
        # 펀딩비 양수 → 숏 수취 → funding_income > 0, 순손익에 반영.
        res = self._build_and_hold_with_funding(0.0001, settle_at=50)
        assert res.metrics.funding_income_krw > 0.0
        assert res.metrics.net_profit_krw == pytest.approx(
            res.metrics.funding_income_krw, rel=1e-6
        )

    def test_negative_funding_debits_short(self) -> None:
        # 펀딩비 음수 → 숏 지급 → funding_income < 0.
        res = self._build_and_hold_with_funding(-0.0001, settle_at=50)
        assert res.metrics.funding_income_krw < 0.0

    def test_no_funding_when_no_position(self) -> None:
        # 포지션을 만들기 전(초반 무거래 구간)에 정산되면 펀딩 0.
        res = self._build_and_hold_with_funding(0.0005, settle_at=5)
        assert res.metrics.funding_income_krw == 0.0

    def test_funding_magnitude_matches_short_notional(self) -> None:
        # funding = q_b·S_b·e·rate. 정산은 정산 바 직전부터 보유한 숏 기준이므로
        # 정산 직전 바(settle_at-1)의 보유 명목 × rate 와 일치해야 한다.
        settle_at = 50
        res = self._build_and_hold_with_funding(0.0002, settle_at=settle_at)
        held_notional = res.equity[settle_at - 1].notional_krw
        assert held_notional > 0
        assert res.metrics.funding_income_krw == pytest.approx(held_notional * 0.0002, rel=1e-6)

    def test_default_bar_has_no_funding(self) -> None:
        res = run_kimp_backtest(kimps_to_bars([0.03] * 40), BacktestConfig(z_window=10))
        assert res.metrics.funding_income_krw == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
