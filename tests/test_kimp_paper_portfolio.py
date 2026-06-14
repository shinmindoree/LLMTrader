"""``live.kimp_paper_portfolio`` 단위테스트.

네트워크/DB 없이 순수 헬퍼(슬롯 선택, 슬롯 파라미터, 상태 집계, 슬롯 동기화)를
검증한다. 랭킹/시세 적재는 IO라 여기서 다루지 않는다.
"""

from __future__ import annotations

import pytest

from api.schemas import KimpPaperPortfolioParams
from live.kimp_neutral import KimpQuote
from live.kimp_paper_portfolio import (
    _desired_symbols,
    _make_slot,
    _PortfolioState,
    _slot_params,
    _sync_slots,
    build_status,
)


def _pf_params(**kw: object) -> KimpPaperPortfolioParams:
    base: dict[str, object] = {"top_n": 3, "capital_per_slot_krw": 5_000_000.0}
    base.update(kw)
    return KimpPaperPortfolioParams(**base)  # type: ignore[arg-type]


class TestDesiredSymbols:
    def test_picks_top_n_with_valid_scores(self) -> None:
        ranked = [("AAA", 1.5), ("BBB", 0.9), ("CCC", 0.2), ("DDD", -0.5)]
        assert _desired_symbols(ranked, 2) == ["AAA", "BBB"]

    def test_skips_none_scores(self) -> None:
        ranked = [("AAA", None), ("BBB", 0.9), ("CCC", None), ("DDD", 0.1)]
        assert _desired_symbols(ranked, 3) == ["BBB", "DDD"]

    def test_fewer_than_top_n(self) -> None:
        ranked = [("AAA", 1.0)]
        assert _desired_symbols(ranked, 5) == ["AAA"]

    def test_empty(self) -> None:
        assert _desired_symbols([], 3) == []


class TestSlotParams:
    def test_carries_portfolio_knobs(self) -> None:
        pf = _pf_params(
            capital_per_slot_krw=7_000_000.0,
            full_build_z=-2.5,
            flat_z=0.7,
            leverage=2.0,
            hedge_mode="delta",
        )
        sp = _slot_params("ETH", pf)
        assert sp.symbol == "ETH"
        assert sp.mode == "paper"
        assert sp.gross_cap_krw == 7_000_000.0
        assert sp.full_build_z == -2.5
        assert sp.flat_z == 0.7
        assert sp.leverage == 2.0
        assert sp.hedge_mode == "delta"

    def test_make_slot_is_paper(self) -> None:
        st = _make_slot("u1", "BTC", _pf_params(), None)
        assert st.paper is True
        assert st.symbol == "BTC"
        assert st.running is True
        assert st.params is not None and st.params.symbol == "BTC"


class TestBuildStatus:
    def test_aggregates_totals_and_sorts_by_score(self) -> None:
        pf = _PortfolioState(user_id="u1", params=_pf_params(top_n=2), running=True)
        a = _make_slot("u1", "AAA", pf.params, None)
        a.current_notional_krw = 1_000_000.0
        a.mtm_pnl_krw = 50_000.0
        a.accumulated_fee_krw = 2_000.0
        b = _make_slot("u1", "BBB", pf.params, None)
        b.current_notional_krw = 2_000_000.0
        b.mtm_pnl_krw = -10_000.0
        b.accumulated_fee_krw = 3_000.0
        pf.slots = {"AAA": a, "BBB": b}
        pf.scores = {"AAA": 0.3, "BBB": 1.2}

        status = build_status(pf)
        assert status.running is True
        assert status.n_slots == 2
        assert status.total_notional_krw == 3_000_000.0
        assert status.total_unrealized_pnl_krw == 40_000.0
        assert status.total_fee_krw == 5_000.0
        # 점수 내림차순: BBB(1.2) 먼저.
        assert [s.symbol for s in status.slots] == ["BBB", "AAA"]
        assert status.slots[0].score == 1.2

    def test_empty_portfolio(self) -> None:
        pf = _PortfolioState(user_id="u1", params=_pf_params(), running=False)
        status = build_status(pf)
        assert status.running is False
        assert status.n_slots == 0
        assert status.total_notional_krw == 0.0


@pytest.mark.asyncio
class TestSyncSlots:
    async def test_adds_and_removes_slots(self) -> None:
        pf = _PortfolioState(user_id="u1", params=_pf_params(top_n=3), running=True)
        await _sync_slots(pf, ["AAA", "BBB"])
        assert set(pf.slots.keys()) == {"AAA", "BBB"}

        # 보유 없는 슬롯은 청산 후 제거된다(페이퍼라 IO 없음).
        await _sync_slots(pf, ["BBB", "CCC"])
        assert set(pf.slots.keys()) == {"BBB", "CCC"}

    async def test_keeps_existing_slot_instances(self) -> None:
        pf = _PortfolioState(user_id="u1", params=_pf_params(top_n=2), running=True)
        await _sync_slots(pf, ["AAA"])
        first = pf.slots["AAA"]
        first.upbit_long_qty = 1.5
        await _sync_slots(pf, ["AAA", "BBB"])
        # 기존 슬롯 객체가 유지되어 누적 상태가 보존된다.
        assert pf.slots["AAA"] is first
        assert pf.slots["AAA"].upbit_long_qty == 1.5

    async def test_removed_flat_slot_liquidation_noop(self) -> None:
        pf = _PortfolioState(user_id="u1", params=_pf_params(top_n=1), running=True)
        await _sync_slots(pf, ["AAA"])
        st = pf.slots["AAA"]
        _ = KimpQuote(symbol="AAA", upbit_krw=1.0, binance_usdt=1.0, usd_krw=1.0)
        await _sync_slots(pf, ["BBB"])
        assert "AAA" not in pf.slots
        assert st.running is False
