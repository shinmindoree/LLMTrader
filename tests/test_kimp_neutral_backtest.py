"""``live.kimp_neutral_backtest`` 결정적 단위테스트.

합성 김프 시계열로 단순 진입/청산, 수수료, 펀딩, 손익 분해를 검증한다.
"""

from __future__ import annotations

import math

import pytest

from live.kimp_neutral import HedgeMode
from live.kimp_neutral_backtest import (
    BacktestConfig,
    BacktestMetrics,
    KimpBacktestResult,
    KimpBar,
    composite_score,
    run_kimp_backtest,
)

S_B = 1000.0  # 바이낸스 USDT/coin
E = 1400.0  # USD/KRW

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


def kimps_to_bars(kimps: list[float]) -> list[KimpBar]:
    return _series(kimps)


def _zero_fee_config(**kwargs: object) -> BacktestConfig:
    return BacktestConfig(upbit_taker_fee=0.0, binance_taker_fee=0.0, **kwargs)


class TestDegenerate:
    def test_empty(self) -> None:
        res = run_kimp_backtest([], BacktestConfig())
        assert res.metrics.n_bars == 0
        assert res.metrics.net_profit_krw == 0.0
        assert res.equity == []

    def test_single_bar_no_trade_when_not_reverse_kimp(self) -> None:
        res = run_kimp_backtest([_bar(0, 0.03)], BacktestConfig())
        assert res.metrics.n_bars == 1
        assert res.metrics.n_entries == 0
        assert res.metrics.net_profit_krw == 0.0


class TestSimpleEntryExit:
    def test_enters_once_and_exits_once_at_target_kimp(self) -> None:
        bars = kimps_to_bars([-0.03, -0.02, 0.006])
        res = run_kimp_backtest(bars, _zero_fee_config(full_build_z=-2.0, flat_z=0.5))

        assert res.metrics.n_entries == 1
        assert res.metrics.n_exits == 1
        assert res.metrics.completed_trades == 1
        assert res.metrics.n_rebalances == 2
        assert res.metrics.kimp_pnl_krw > 0.0
        assert res.metrics.net_profit_krw == pytest.approx(res.metrics.kimp_pnl_krw)

    def test_no_rebalance_while_position_is_open(self) -> None:
        bars = kimps_to_bars([-0.03, -0.031, -0.029, -0.032, -0.03])
        res = run_kimp_backtest(bars, _zero_fee_config(full_build_z=-2.0, flat_z=0.5))

        assert res.metrics.n_entries == 1
        # 기간 종료 청산 1회만 추가된다. 중간 김프 변화는 추가 리밸런싱을 만들지 않는다.
        assert res.metrics.n_exits == 1
        assert res.metrics.n_rebalances == 2

    def test_does_not_enter_when_fee_adjusted_edge_is_negative(self) -> None:
        bars = kimps_to_bars([-0.001, 0.001, 0.002])
        res = run_kimp_backtest(
            bars,
            BacktestConfig(
                full_build_z=-0.1,
                flat_z=0.1,
                upbit_taker_fee=0.002,
                binance_taker_fee=0.002,
            ),
        )

        assert res.metrics.n_entries == 0
        assert res.metrics.net_profit_krw == 0.0


class TestNeutrality:
    @staticmethod
    def _build_then_common_move() -> list[KimpBar]:
        kimps = [-0.03] * 40
        prices = [1000.0 + i * 10.0 for i in range(len(kimps))]
        return [
            KimpBar(
                ts_ms=i * _MINUTE,
                upbit_krw=prices[i] * E * (1 + kimps[i]),
                binance_usdt=prices[i],
                usd_krw=E,
            )
            for i in range(len(kimps))
        ]

    def test_delta_mode_tighter_than_quantity_under_common_move(self) -> None:
        bars = self._build_then_common_move()
        q = run_kimp_backtest(
            bars,
            _zero_fee_config(full_build_z=-2.0, flat_z=0.5, hedge_mode=HedgeMode.QUANTITY),
        )
        d = run_kimp_backtest(
            bars,
            _zero_fee_config(full_build_z=-2.0, flat_z=0.5, hedge_mode=HedgeMode.DELTA),
        )

        assert abs(d.metrics.net_profit_krw) < abs(q.metrics.net_profit_krw)
        assert abs(d.metrics.net_profit_krw) < 0.005 * 1e7


class TestKimpPnl:
    def test_kimp_widening_is_profitable(self) -> None:
        kimps = [-0.03, -0.02, 0.00, 0.006]
        res = run_kimp_backtest(
            kimps_to_bars(kimps),
            _zero_fee_config(full_build_z=-2.0, flat_z=0.5),
        )
        assert res.metrics.kimp_pnl_krw > 0.0
        assert res.metrics.net_profit_krw > 0.0

    def test_fees_reduce_profit(self) -> None:
        kimps = [-0.03, -0.02, 0.00, 0.006]
        cfg_free = _zero_fee_config(full_build_z=-2.0, flat_z=0.5)
        cfg_fee = BacktestConfig(
            full_build_z=-2.0,
            flat_z=0.5,
            upbit_taker_fee=0.001,
            binance_taker_fee=0.001,
        )
        free = run_kimp_backtest(kimps_to_bars(kimps), cfg_free)
        fee = run_kimp_backtest(kimps_to_bars(kimps), cfg_fee)
        assert fee.metrics.fee_drag_krw > 0.0
        assert fee.metrics.net_profit_krw < free.metrics.net_profit_krw
        assert fee.metrics.net_profit_krw == pytest.approx(
            fee.metrics.kimp_pnl_krw + fee.metrics.funding_income_krw - fee.metrics.fee_drag_krw
        )


class TestMetrics:
    def test_metrics_are_finite_and_consistent(self) -> None:
        kimps = [-0.03 + 0.01 * math.sin(i / 5.0) for i in range(200)]
        res = run_kimp_backtest(
            kimps_to_bars(kimps),
            BacktestConfig(full_build_z=-2.0, flat_z=0.5),
        )
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
        res = run_kimp_backtest(
            kimps_to_bars([0.03] * 10), BacktestConfig(gross_cap_krw=5e7)
        )
        assert res.equity[0].equity_krw == pytest.approx(5e7)


class TestFunding:
    @staticmethod
    def _build_and_hold_with_funding(rate: float | None, settle_at: int) -> KimpBacktestResult:
        kimps = [-0.03] * 30
        bars: list[KimpBar] = []
        for i, k in enumerate(kimps):
            r = rate if i == settle_at else None
            bars.append(_bar_funding(i, k, r))
        return run_kimp_backtest(
            bars,
            _zero_fee_config(full_build_z=-2.0, flat_z=0.5),
        )

    def test_positive_funding_credits_short(self) -> None:
        res = self._build_and_hold_with_funding(0.0001, settle_at=10)
        assert res.metrics.funding_income_krw > 0.0
        assert res.metrics.funding_event_count == 1
        assert res.metrics.net_profit_krw == pytest.approx(
            res.metrics.kimp_pnl_krw + res.metrics.funding_income_krw, rel=1e-6
        )

    def test_negative_funding_debits_short(self) -> None:
        res = self._build_and_hold_with_funding(-0.0001, settle_at=10)
        assert res.metrics.funding_income_krw < 0.0
        assert res.metrics.funding_event_count == 1

    def test_no_funding_when_no_position(self) -> None:
        bars = [_bar_funding(0, 0.02, 0.0005), _bar_funding(1, -0.03, None)]
        res = run_kimp_backtest(bars, _zero_fee_config(full_build_z=-2.0, flat_z=0.5))
        assert res.metrics.funding_income_krw == 0.0
        assert res.metrics.funding_event_count == 0

    def test_funding_magnitude_matches_short_notional(self) -> None:
        settle_at = 10
        res = self._build_and_hold_with_funding(0.0002, settle_at=settle_at)
        q_u = 1e7 / (S_B * E * (1 - 0.03))
        expected_short_notional = q_u * S_B * E
        assert res.metrics.funding_income_krw == pytest.approx(
            expected_short_notional * 0.0002, rel=1e-6
        )

    def test_default_bar_has_no_funding(self) -> None:
        res = run_kimp_backtest(kimps_to_bars([-0.03] * 40), BacktestConfig())
        assert res.metrics.funding_income_krw == 0.0


def _metrics(
    *, total_return_pct: float, max_drawdown_pct: float = 0.0, sharpe: float = 0.0
) -> BacktestMetrics:
    return BacktestMetrics(
        n_bars=100,
        total_return_pct=total_return_pct,
        net_profit_krw=total_return_pct / 100.0 * 1e7,
        kimp_pnl_krw=total_return_pct / 100.0 * 1e7,
        funding_income_krw=0.0,
        funding_event_count=0,
        max_drawdown_pct=max_drawdown_pct,
        sharpe=sharpe,
        n_rebalances=2,
        n_entries=1,
        n_exits=1,
        completed_trades=1,
        fee_drag_krw=0.0,
        avg_kimp_pct=0.0,
        time_in_market_pct=50.0,
        final_kimp_pct=0.0,
    )


class TestCompositeScore:
    def test_higher_return_scores_higher(self) -> None:
        assert composite_score(_metrics(total_return_pct=5.0)) > composite_score(
            _metrics(total_return_pct=2.0)
        )

    def test_drawdown_penalizes(self) -> None:
        low_dd = composite_score(_metrics(total_return_pct=5.0, max_drawdown_pct=1.0))
        high_dd = composite_score(_metrics(total_return_pct=5.0, max_drawdown_pct=20.0))
        assert low_dd > high_dd

    def test_sharpe_adds_small_bonus(self) -> None:
        base = composite_score(_metrics(total_return_pct=5.0))
        with_sharpe = composite_score(_metrics(total_return_pct=5.0, sharpe=2.0))
        assert with_sharpe > base

    def test_nonfinite_sharpe_is_safe(self) -> None:
        s = composite_score(_metrics(total_return_pct=3.0, sharpe=float("nan")))
        assert math.isfinite(s)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
