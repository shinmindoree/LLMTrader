"""``live.kimp_neutral`` 순수 로직 단위테스트.

핵심 목적은 **델타-중립성 증명**이다. 과거 설명의 "업비트 매도 + 바이낸스 신규
롱 = 네이키드 롱" 오류를 회귀 방지하기 위해, 매칭북은 방향성 델타가 0 근처이고
네이키드 다리는 크게 노출됨을 명시적으로 검증한다.
"""

from __future__ import annotations

import pytest

from live.kimp_neutral import (
    HedgeMode,
    KimpQuote,
    LotFilter,
    LotPair,
    NeutralBook,
    RebalanceAction,
    SignalConfig,
    SizingConfig,
    book_deltas,
    plan_entry,
    plan_rebalance,
    realized_pnl,
    target_book_krw,
)

# 마찰 없는(틱 제약 없는) 시세: 순수 수학 검증용.
S_B = 1000.0      # 바이낸스 USDT/coin
E = 1400.0        # USD/KRW
K = 0.03          # 김프 3%
S_U = S_B * E * (1 + K)  # 업비트 KRW/coin = 1,442,000


def _quote(kimp: float = K, s_b: float = S_B, e: float = E) -> KimpQuote:
    return KimpQuote(symbol="BTC", upbit_krw=s_b * e * (1 + kimp), binance_usdt=s_b, usd_krw=e)


class TestKimpQuote:
    def test_kimp_formula(self) -> None:
        q = _quote(0.0345)
        assert q.kimp == pytest.approx(0.0345)
        assert q.binance_krw == pytest.approx(S_B * E)

    def test_rejects_nonpositive(self) -> None:
        for upbit, binance, fx in (
            (0.0, 1.0, 1.0),
            (1.0, -1.0, 1.0),
            (1.0, 1.0, 0.0),
        ):
            with pytest.raises(ValueError):
                KimpQuote(symbol="X", upbit_krw=upbit, binance_usdt=binance, usd_krw=fx)


class TestNeutrality:
    """방향성 델타 분해 — 이 클래스가 핵심 회귀 가드."""

    def test_naked_long_is_directional(self) -> None:
        # 업비트 롱만(숏 0) = 네이키드 롱: 공통 가격변동 델타가 명목 전체.
        q = _quote()
        book = NeutralBook("BTC", upbit_long_qty=5.0, binance_short_qty=0.0, entry=q)
        d = book_deltas(book, q)
        assert d.coin_delta_qty == pytest.approx(5.0)
        assert d.price_delta_krw == pytest.approx(5.0 * S_U)  # 전액 노출
        assert d.price_delta_krw > 0.5 * d.upbit_notional_krw

    def test_quantity_matched_has_only_kimp_leakage(self) -> None:
        # q_b = q_u → 코인 델타 0, 가격 델타 = S_b·e·q·k (작은 누수).
        q = _quote()
        book = NeutralBook(
            "BTC", upbit_long_qty=5.0, binance_short_qty=5.0, entry=q,
            hedge_mode=HedgeMode.QUANTITY,
        )
        d = book_deltas(book, q)
        assert d.coin_delta_qty == pytest.approx(0.0)
        assert d.price_delta_krw == pytest.approx(5.0 * S_B * E * K)
        # 누수는 명목의 ~k 수준(여기선 3%)으로 작다.
        assert abs(d.price_delta_krw) < 0.05 * d.upbit_notional_krw

    def test_delta_matched_is_fully_neutral(self) -> None:
        # q_b = q_u·(1+k) → 공통 가격변동 델타가 정확히 0.
        q = _quote()
        book = NeutralBook(
            "BTC", upbit_long_qty=5.0, binance_short_qty=5.0 * (1 + K), entry=q,
            hedge_mode=HedgeMode.DELTA,
        )
        d = book_deltas(book, q)
        assert d.price_delta_krw == pytest.approx(0.0, abs=1e-6)

    def test_fx_exposure_is_margin_at_entry(self) -> None:
        q = _quote()
        margin = 5.0 * S_B / 2.0  # lev=2
        book = NeutralBook(
            "BTC", upbit_long_qty=5.0, binance_short_qty=5.0, entry=q,
            binance_margin_usdt=margin,
        )
        d = book_deltas(book, q)
        assert d.fx_exposure_usd == pytest.approx(margin)
        assert d.fx_hedge_usd == pytest.approx(margin)

    def test_fx_exposure_grows_when_short_gains(self) -> None:
        q0 = _quote()
        margin = 5.0 * S_B / 2.0
        book = NeutralBook(
            "BTC", upbit_long_qty=5.0, binance_short_qty=5.0, entry=q0,
            binance_margin_usdt=margin,
        )
        # 가격 하락 → 숏 이익 → USD 자산(미실현) 증가.
        q1 = _quote(s_b=900.0)
        d = book_deltas(book, q1)
        assert d.fx_exposure_usd > margin
        assert d.fx_exposure_usd == pytest.approx(margin + 5.0 * (S_B - 900.0))


class TestPlanEntry:
    def _lots(self) -> LotPair:
        return LotPair(
            upbit=LotFilter(step_size=0.0001, min_qty=0.0001, min_notional_quote=5000.0),
            binance=LotFilter(step_size=0.001, min_qty=0.001, min_notional_quote=5.0),
        )

    def test_capital_is_conserved(self) -> None:
        q = _quote()
        cfg = SizingConfig(hedge_mode=HedgeMode.QUANTITY, leverage=1.0)
        plan = plan_entry(q, capital_krw=14_420_000.0, lots=self._lots(), config=cfg)
        assert plan.ok
        # 업비트 비용 + 담보 KRW = 투입 자본 (한 step 명목 이내로 근접, 초과 금지).
        assert plan.total_capital_krw <= 14_420_000.0 + 1.0
        coarse_notional = 0.001 * (S_U + S_B * E)
        assert 14_420_000.0 - plan.total_capital_krw < coarse_notional

    def test_entry_book_is_neutral(self) -> None:
        q = _quote()
        for mode in (HedgeMode.QUANTITY, HedgeMode.DELTA):
            cfg = SizingConfig(hedge_mode=mode, leverage=1.0)
            plan = plan_entry(q, 14_420_000.0, self._lots(), cfg)
            assert plan.ok
            d = book_deltas(plan.to_book(), q)
            if mode is HedgeMode.DELTA:
                # 한 step 누수 이내.
                assert abs(d.price_delta_krw) < 2.0 * 0.001 * S_B * E
            else:
                assert d.price_delta_krw == pytest.approx(plan.upbit_long_qty * S_B * E * K)

    def test_leverage_allows_larger_book(self) -> None:
        q = _quote()
        lots = self._lots()
        p1 = plan_entry(q, 5_000_000.0, lots, SizingConfig(leverage=1.0))
        p3 = plan_entry(q, 5_000_000.0, lots, SizingConfig(leverage=3.0))
        assert p1.ok and p3.ok
        assert p3.upbit_long_qty > p1.upbit_long_qty

    def test_rejects_insufficient_capital(self) -> None:
        q = _quote()
        plan = plan_entry(q, 100.0, self._lots(), SizingConfig())
        assert not plan.ok
        assert plan.reason is not None

    def test_rejects_nonpositive_capital(self) -> None:
        plan = plan_entry(_quote(), 0.0, self._lots(), SizingConfig())
        assert not plan.ok

    def test_delta_mode_shorts_more_than_long(self) -> None:
        q = _quote()
        plan = plan_entry(q, 14_420_000.0, self._lots(), SizingConfig(hedge_mode=HedgeMode.DELTA))
        assert plan.ok
        assert plan.binance_short_qty > plan.upbit_long_qty


class TestRealizedPnl:
    def test_pure_kimp_move_equals_kimp_component(self) -> None:
        # S_b, e 고정 + 김프 0.03→0.05 → 손익은 순수 김프, 잔차 0.
        entry = _quote(kimp=0.03)
        book = NeutralBook("BTC", 5.0, 5.0, entry, binance_margin_usdt=5.0 * S_B)
        exit_q = _quote(kimp=0.05)
        pnl = realized_pnl(book, exit_q)
        assert pnl.binance_leg_krw == pytest.approx(0.0)
        assert pnl.kimp_component_krw == pytest.approx(5.0 * S_B * E * 0.02)
        assert pnl.residual_krw == pytest.approx(0.0, abs=1e-3)
        assert pnl.total_krw == pytest.approx(pnl.kimp_component_krw)
        assert pnl.total_krw > 0  # Long-Kimp 북은 김프 확대 시 이익

    def test_hedge_removes_common_move(self) -> None:
        # 공통 +10% 가격변동, 김프 고정: 헤지북 손익 << 네이키드 손익.
        entry = _quote(kimp=0.03, s_b=1000.0)
        exit_q = _quote(kimp=0.03, s_b=1100.0)
        hedged = NeutralBook("BTC", 5.0, 5.0, entry, binance_margin_usdt=5.0 * 1000.0)
        naked = NeutralBook("BTC", 5.0, 0.0, entry)
        p_h = realized_pnl(hedged, exit_q)
        p_n = realized_pnl(naked, exit_q)
        assert abs(p_h.total_krw) < 0.05 * abs(p_n.total_krw)
        # 헤지 잔여는 김프 누수(q·k·ΔS_b·e)와 일치.
        assert p_h.total_krw == pytest.approx(5.0 * 0.03 * 100.0 * E)

    def test_fee_reduces_total(self) -> None:
        entry = _quote(kimp=0.03)
        book = NeutralBook("BTC", 5.0, 5.0, entry)
        exit_q = _quote(kimp=0.05)
        gross = realized_pnl(book, exit_q).total_krw
        net = realized_pnl(book, exit_q, fee_krw=10_000.0).total_krw
        assert net == pytest.approx(gross - 10_000.0)


class TestTargetBookKrw:
    def _cfg(self) -> SignalConfig:
        return SignalConfig(gross_cap_krw=1e8, full_build_z=-2.0, flat_z=0.5)

    def test_full_size_when_cheap(self) -> None:
        assert target_book_krw(-3.0, self._cfg()) == pytest.approx(1e8)
        assert target_book_krw(-2.0, self._cfg()) == pytest.approx(1e8)

    def test_flat_when_expensive(self) -> None:
        assert target_book_krw(0.5, self._cfg()) == pytest.approx(0.0)
        assert target_book_krw(3.0, self._cfg()) == pytest.approx(0.0)

    def test_linear_in_between(self) -> None:
        # z=-0.75 는 [-2, 0.5] 의 중점 → 0.5·cap.
        assert target_book_krw(-0.75, self._cfg()) == pytest.approx(0.5e8)

    def test_monotonic_decreasing(self) -> None:
        cfg = self._cfg()
        zs = [-3, -2, -1, 0, 0.5, 1]
        vals = [target_book_krw(z, cfg) for z in zs]
        assert all(a >= b for a, b in zip(vals, vals[1:], strict=False))


class TestPlanRebalance:
    def _lots(self) -> LotPair:
        return LotPair(
            upbit=LotFilter(step_size=0.0001),
            binance=LotFilter(step_size=0.001),
        )

    def test_scale_up_moves_both_legs(self) -> None:
        q = _quote()
        order = plan_rebalance(5e7, 8e7, q, self._lots(), SizingConfig())
        assert order.action is RebalanceAction.SCALE_UP
        assert order.upbit_side == "BUY"
        assert order.binance_side == "SELL"  # 숏 추가
        assert order.upbit_qty > 0 and order.binance_qty > 0

    def test_scale_down_moves_both_legs(self) -> None:
        q = _quote()
        order = plan_rebalance(8e7, 5e7, q, self._lots(), SizingConfig())
        assert order.action is RebalanceAction.SCALE_DOWN
        assert order.upbit_side == "SELL"
        assert order.binance_side == "BUY"  # 숏 환매

    def test_tiny_diff_holds(self) -> None:
        q = _quote()
        order = plan_rebalance(5e7, 5e7 + 1.0, q, self._lots(), SizingConfig())
        assert order.action is RebalanceAction.HOLD

    def test_delta_mode_shorts_more(self) -> None:
        q = _quote()
        order = plan_rebalance(
            5e7, 8e7, q, self._lots(), SizingConfig(hedge_mode=HedgeMode.DELTA)
        )
        assert order.binance_qty >= order.upbit_qty


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
