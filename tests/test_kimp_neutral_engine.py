"""``live.kimp_neutral_engine`` 단위테스트.

거래소/DB 의존을 페이크 :class:`KimpExecutor` 로 대체해 의사결정(plan_tick),
주문 적용(_apply_order), 틱 통합(_tick), 직렬화 라운드트립, 마진 de-risk,
체결북 중립성을 검증한다.
"""

from __future__ import annotations

import pytest

from api.schemas import KimpArbitrageParams
from live.kimp_neutral import KimpQuote, RebalanceAction, book_deltas
from live.kimp_neutral_engine import (
    _apply_order,
    _KimpEngineState,
    _refresh_metrics,
    _state_to_dict,
    _tick,
    compute_zscore,
    get_engine_status,
    plan_tick,
    status_from_dict,
)

S_B = 1000.0
E = 1400.0
K = 0.03


def _quote(kimp: float = K, s_b: float = S_B, e: float = E) -> KimpQuote:
    return KimpQuote(symbol="BTC", upbit_krw=s_b * e * (1 + kimp), binance_usdt=s_b, usd_krw=e)


def _params(**kw: object) -> KimpArbitrageParams:
    base: dict[str, object] = {
        "symbol": "BTC",
        "gross_cap_krw": 10_000_000.0,
        "full_build_z": -2.0,
        "flat_z": 0.5,
        "leverage": 1.0,
    }
    base.update(kw)
    return KimpArbitrageParams(**base)  # type: ignore[arg-type]


def _state(**kw: object) -> _KimpEngineState:
    st = _KimpEngineState(user_id="u1", symbol="BTC", params=_params())
    for k, v in kw.items():
        setattr(st, k, v)
    return st


class FakeExecutor:
    def __init__(
        self,
        quote: KimpQuote,
        *,
        z: float | None = None,
        margin_ratio: float | None = None,
        fill_ratio: float = 1.0,
    ) -> None:
        self.quote = quote
        self.z = z
        self.margin_ratio = margin_ratio
        self.fill_ratio = fill_ratio
        self.calls: list[tuple[str, str, float]] = []

    async def fetch_quote(self, symbol: str) -> KimpQuote:
        return self.quote

    async def fetch_zscore(self, symbol: str, window_days: int) -> float | None:
        return self.z

    async def fetch_margin_ratio(self) -> float | None:
        return self.margin_ratio

    async def buy_upbit(self, symbol: str, qty: float, price_krw: float) -> float:
        self.calls.append(("buy_upbit", symbol, qty))
        return qty * self.fill_ratio

    async def sell_upbit(self, symbol: str, qty: float) -> float:
        self.calls.append(("sell_upbit", symbol, qty))
        return qty * self.fill_ratio

    async def open_short(self, symbol: str, qty: float) -> float:
        self.calls.append(("open_short", symbol, qty))
        return qty * self.fill_ratio

    async def cover_short(self, symbol: str, qty: float) -> float:
        self.calls.append(("cover_short", symbol, qty))
        return qty * self.fill_ratio


class TestComputeZscore:
    def test_normal(self) -> None:
        assert compute_zscore(0.05, 0.03, 0.01) == pytest.approx(2.0)

    def test_none_when_no_stats(self) -> None:
        assert compute_zscore(0.05, None, 0.01) is None
        assert compute_zscore(0.05, 0.03, None) is None
        assert compute_zscore(0.05, 0.03, 0.0) is None


class TestPlanTick:
    def test_z_none_holds(self) -> None:
        d = plan_tick(
            upbit_long_qty=0.0, quote=_quote(), z=None, margin_ratio=None, params=_params()
        )
        assert d.order.action is RebalanceAction.HOLD
        assert d.target_notional_krw == 0.0

    def test_cheap_builds_full(self) -> None:
        d = plan_tick(
            upbit_long_qty=0.0, quote=_quote(), z=-3.0, margin_ratio=None, params=_params()
        )
        assert d.order.action is RebalanceAction.SCALE_UP
        assert d.target_notional_krw == pytest.approx(10_000_000.0)
        assert d.order.upbit_side == "BUY"
        assert d.order.binance_side == "SELL"

    def test_expensive_unwinds(self) -> None:
        # 보유 중인데 z가 비싸짐 → 청산(SCALE_DOWN, 목표 0).
        q = _quote()
        qty = 10_000_000.0 / q.upbit_krw
        d = plan_tick(upbit_long_qty=qty, quote=q, z=1.0, margin_ratio=None, params=_params())
        assert d.target_notional_krw == pytest.approx(0.0)
        assert d.order.action is RebalanceAction.SCALE_DOWN
        assert d.order.upbit_side == "SELL"
        assert d.order.binance_side == "BUY"

    def test_margin_derisk_overrides_signal(self) -> None:
        # 신호는 풀빌드(z=-3)지만 마진 위험 → 북을 절반으로 축소.
        q = _quote()
        qty = 10_000_000.0 / q.upbit_krw
        d = plan_tick(
            upbit_long_qty=qty,
            quote=q,
            z=-3.0,
            margin_ratio=0.9,
            params=_params(margin_alert_ratio=0.8),
        )
        assert d.derisk is True
        assert d.order.action is RebalanceAction.SCALE_DOWN
        assert d.target_notional_krw == pytest.approx(10_000_000.0 * 0.5)


class TestApplyOrder:
    @pytest.mark.asyncio
    async def test_scale_up_from_flat(self) -> None:
        q = _quote()
        st = _state()
        d = plan_tick(upbit_long_qty=0.0, quote=q, z=-3.0, margin_ratio=None, params=st.params)
        ex = FakeExecutor(q)
        await _apply_order(st, d.order, q, ex)
        assert st.upbit_long_qty > 0.0
        assert st.binance_short_qty > 0.0
        assert st.entry_quote is q
        assert st.binance_margin_usdt > 0.0
        assert st.accumulated_fee_krw > 0.0
        assert [c[0] for c in ex.calls] == ["buy_upbit", "open_short"]

    @pytest.mark.asyncio
    async def test_executed_book_is_neutral(self) -> None:
        q = _quote()
        st = _state()
        d = plan_tick(upbit_long_qty=0.0, quote=q, z=-3.0, margin_ratio=None, params=st.params)
        await _apply_order(st, d.order, q, FakeExecutor(q))
        deltas = book_deltas(st.book(), q)
        # QUANTITY 모드: 코인 델타 0, 가격 델타는 작은 김프 누수만.
        assert deltas.coin_delta_qty == pytest.approx(0.0)
        assert abs(deltas.price_delta_krw) < 0.05 * deltas.upbit_notional_krw

    @pytest.mark.asyncio
    async def test_scale_down_to_flat_clears_state(self) -> None:
        q = _quote()
        qty = 10_000_000.0 / q.upbit_krw
        st = _state(
            upbit_long_qty=qty, binance_short_qty=qty, entry_quote=q, binance_margin_usdt=qty * S_B
        )
        d = plan_tick(upbit_long_qty=qty, quote=q, z=1.0, margin_ratio=None, params=st.params)
        ex = FakeExecutor(q)
        await _apply_order(st, d.order, q, ex)
        assert st.upbit_long_qty == pytest.approx(0.0)
        assert st.binance_short_qty == pytest.approx(0.0)
        assert st.entry_quote is None
        assert st.binance_margin_usdt == 0.0
        assert [c[0] for c in ex.calls] == ["sell_upbit", "cover_short"]

    @pytest.mark.asyncio
    async def test_partial_fill_reflected(self) -> None:
        q = _quote()
        st = _state()
        d = plan_tick(upbit_long_qty=0.0, quote=q, z=-3.0, margin_ratio=None, params=st.params)
        ex = FakeExecutor(q, fill_ratio=0.5)
        await _apply_order(st, d.order, q, ex)
        assert st.upbit_long_qty == pytest.approx(d.order.upbit_qty * 0.5)
        assert st.binance_short_qty == pytest.approx(d.order.binance_qty * 0.5)

    @pytest.mark.asyncio
    async def test_hold_does_nothing(self) -> None:
        q = _quote()
        st = _state()
        d = plan_tick(upbit_long_qty=0.0, quote=q, z=None, margin_ratio=None, params=st.params)
        ex = FakeExecutor(q)
        await _apply_order(st, d.order, q, ex)
        assert ex.calls == []
        assert st.upbit_long_qty == 0.0


class TestTickIntegration:
    @pytest.mark.asyncio
    async def test_tick_builds_then_unwinds(self) -> None:
        st = _state()
        q = _quote()

        # 1) 김프 쌈 → 진입
        await _tick(st, FakeExecutor(q, z=-3.0))
        assert st.upbit_long_qty > 0.0
        assert st.binance_short_qty > 0.0
        assert st.current_z == -3.0
        assert st.current_kimp == pytest.approx(K)

        # 2) 김프 비쌈 → 청산
        await _tick(st, FakeExecutor(q, z=1.0))
        assert st.upbit_long_qty == pytest.approx(0.0)
        assert st.binance_short_qty == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_tick_margin_derisk(self) -> None:
        q = _quote()
        st = _state()
        await _tick(st, FakeExecutor(q, z=-3.0))  # 진입
        built = st.upbit_long_qty
        assert built > 0.0
        # 마진 위험 틱 → 북 축소
        await _tick(st, FakeExecutor(q, z=-3.0, margin_ratio=0.95))
        assert st.upbit_long_qty < built
        assert st.binance_margin_ratio == 0.95


class TestSerialization:
    def test_roundtrip(self) -> None:
        q = _quote()
        st = _state(
            running=True,
            upbit_long_qty=6.9,
            binance_short_qty=6.9,
            current_kimp=0.031,
            current_z=-1.2,
            target_notional_krw=9_900_000.0,
            current_notional_krw=9_950_000.0,
            fx_hedge_usd=5000.0,
            accumulated_fee_krw=1234.0,
            binance_margin_ratio=0.42,
        )
        _refresh_metrics(st, q)
        d = _state_to_dict(st)
        resp = status_from_dict(d)
        assert resp.symbol == "BTC"
        assert resp.upbit_long_qty == pytest.approx(6.9)
        assert resp.binance_short_qty == pytest.approx(6.9)
        assert resp.kimp_pct == pytest.approx(q.kimp)
        assert resp.accumulated_fee_krw == pytest.approx(1234.0)
        assert resp.binance_margin_ratio == pytest.approx(0.42)
        assert resp.params is not None
        assert resp.params.symbol == "BTC"

    def test_running_override(self) -> None:
        st = _state(running=True)
        d = _state_to_dict(st)
        assert status_from_dict(d, running_override=False).running is False


class TestRefreshMetrics:
    def test_mtm_accumulates_on_price_move(self) -> None:
        st = _state(upbit_long_qty=5.0, binance_short_qty=5.0, entry_quote=_quote())
        _refresh_metrics(st, _quote())  # prev 설정
        _refresh_metrics(st, _quote(s_b=900.0))  # 공통 -10% 이동
        # QUANTITY 헤지: 공통 이동 누수만 → 명목 대비 작아야 한다.
        assert abs(st.mtm_pnl_krw) < 0.05 * st.current_notional_krw

    def test_flat_book_has_zero_deltas(self) -> None:
        st = _state()
        _refresh_metrics(st, _quote())
        assert st.coin_delta_qty == 0.0
        assert st.price_delta_krw == 0.0
        assert st.fx_hedge_usd == 0.0


class TestEngineStatus:
    def test_status_when_no_engine(self) -> None:
        assert get_engine_status("nonexistent-user").running is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
