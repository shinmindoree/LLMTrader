from __future__ import annotations

from types import SimpleNamespace

from scripts.strategies.multi_factor_portfolio_strategy import MultiFactorPortfolioStrategy

from live.portfolio_engine import StreamBoundStrategyContext

SYMBOL = "BTCUSDT"


class DummyProxy:
    def __init__(self, size: float) -> None:
        self.position = SimpleNamespace(size=size)
        self.enter_long_calls = 0
        self.enter_short_calls = 0
        self.close_position_calls = 0
        self.flip_position_calls = 0

    def enter_long(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        self.enter_long_calls += 1

    def enter_short(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        self.enter_short_calls += 1

    def close_position(
        self,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self.close_position_calls += 1

    def flip_position(
        self,
        target_side: int,
        close_reason: str | None = None,
        entry_reason: str | None = None,
        entry_pct: float | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self.flip_position_calls += 1


class DummyPortfolio:
    def __init__(self, proxy: DummyProxy) -> None:
        self._proxy = proxy
        self._trade_contexts = {SYMBOL: SimpleNamespace(job_id="job-test")}

    def for_symbol(self, symbol: str) -> DummyProxy:
        if symbol != SYMBOL:
            raise KeyError(symbol)
        return self._proxy


def test_mfp_reconcile_resyncs_stream_bound_position_without_duplicate_entry() -> None:
    proxy = DummyProxy(size=0.016)
    ctx = StreamBoundStrategyContext(DummyPortfolio(proxy), symbol=SYMBOL, interval="15m")
    strategy = MultiFactorPortfolioStrategy()
    strategy._committed_side = 0

    assert ctx.position is proxy.position

    strategy._reconcile(ctx, target=1, long_count=3, short_count=0, ts=1)

    assert strategy._committed_side == 1
    assert proxy.enter_long_calls == 0
    assert proxy.enter_short_calls == 0
    assert proxy.close_position_calls == 0
    assert proxy.flip_position_calls == 0
