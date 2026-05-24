"""Defensive wrapper around MultiFactorPortfolioStrategy.

Adds an INCREMENTAL volatility-regime gate that pauses LONG entries when the
market is in a high-volatility breakdown (e.g. May 2021 crash, FTX Nov 2022),
and pauses SHORT entries during high-vol UP-spikes.

Inherits all 17 legs and signal computation from production MFP. Only
overrides _reconcile() to inject the gate. Incremental ATR(14) + EMA(800)
state is maintained on every bar to keep cost O(1) per bar.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from multi_factor_portfolio_strategy import (  # noqa: E402
    MultiFactorPortfolioStrategy,
)
from strategy.context import StrategyContext  # noqa: E402


DEFAULTS: dict[str, Any] = {
    "vol_pause_pct": 0.012,
    "trend_ema_period": 800,
    "block_long_in_bear_spike": True,
    "block_short_in_bull_spike": True,
}


class _EMA:
    __slots__ = ("period", "alpha", "value", "n", "_seed_sum")

    def __init__(self, period: int) -> None:
        self.period = int(period)
        self.alpha = 2.0 / (self.period + 1.0)
        self.value: float = float("nan")
        self.n = 0
        self._seed_sum = 0.0

    def update(self, x: float) -> float:
        if self.n < self.period:
            self._seed_sum += x
            self.n += 1
            if self.n == self.period:
                self.value = self._seed_sum / self.period
            return self.value
        self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value

    @property
    def ready(self) -> bool:
        return self.n >= self.period and math.isfinite(self.value)


class _ATR:
    __slots__ = ("period", "value", "n", "_tr_sum", "_prev_close")

    def __init__(self, period: int) -> None:
        self.period = int(period)
        self.value: float = float("nan")
        self.n = 0
        self._tr_sum = 0.0
        self._prev_close: float | None = None

    def update(self, h: float, l: float, c: float) -> float:
        if self._prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - self._prev_close), abs(l - self._prev_close))
        self._prev_close = c
        if self.n < self.period:
            self._tr_sum += tr
            self.n += 1
            if self.n == self.period:
                self.value = self._tr_sum / self.period
            return self.value
        self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value

    @property
    def ready(self) -> bool:
        return self.n >= self.period and math.isfinite(self.value)


class MultiFactorPortfolioDefensiveStrategy(MultiFactorPortfolioStrategy):
    """MFP + incremental volatility-regime gate."""

    def __init__(self, **kwargs: Any) -> None:
        my_p = {k: kwargs.pop(k, v) for k, v in DEFAULTS.items()}
        super().__init__(**kwargs)
        for k, v in my_p.items():
            setattr(self, k, v)
        self.params = {**self.params, **my_p}
        # Incremental indicators
        self._trend_ema = _EMA(int(self.trend_ema_period))
        self._atr = _ATR(14)
        self._last_close: float = float("nan")
        self._gate_block_long: bool = False
        self._gate_block_short: bool = False

    # Hook into the bar event from MFP: parent's on_bar calls _reconcile last;
    # but to keep state in sync we update our indicators inside on_bar (which
    # runs first on every bar regardless of regime).
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # Only update on new bars (matches parent's new_bar_only gating logic)
        if (not self.new_bar_only) or bool(bar.get("is_new_bar", True)):
            try:
                high = float(bar.get("high", bar.get("price", math.nan)))
                low = float(bar.get("low", bar.get("price", math.nan)))
                close = float(bar.get("close", bar.get("price", math.nan)))
            except (TypeError, ValueError):
                high = low = close = float("nan")
            if all(math.isfinite(v) for v in (high, low, close)) and close > 0:
                self._trend_ema.update(close)
                self._atr.update(high, low, close)
                self._last_close = close
        super().on_bar(ctx, bar)

    def _reconcile(self, ctx: StrategyContext, target: int, long_count: int,
                   short_count: int, ts: int) -> None:
        block_long = block_short = False
        if self._trend_ema.ready and self._atr.ready and math.isfinite(self._last_close) and self._last_close > 0:
            atr_pct = self._atr.value / self._last_close
            high_vol = atr_pct >= float(self.vol_pause_pct)
            below_trend = self._last_close < self._trend_ema.value
            above_trend = self._last_close > self._trend_ema.value
            block_long = bool(high_vol and below_trend and self.block_long_in_bear_spike)
            block_short = bool(high_vol and above_trend and self.block_short_in_bull_spike)

        self._gate_block_long = block_long
        self._gate_block_short = block_short

        # If target blocked, force flatten (close any existing position) and skip
        if target == 1 and block_long:
            cur = self._committed_side
            if cur != 0:
                try:
                    ctx.close_position(reason="MFPD: vol gate -> flatten, block long")
                except Exception:  # noqa: BLE001
                    pass
                self._committed_side = 0
                self._emit_event(ctx, "MFP_FLAT", {
                    "ts": ts, "target": int(target), "prev_side": int(cur),
                    "committed_side": 0,
                    "long_legs": long_count, "short_legs": short_count,
                    "reason": "vol_gate_block_long",
                })
            return
        if target == -1 and block_short:
            cur = self._committed_side
            if cur != 0:
                try:
                    ctx.close_position(reason="MFPD: vol gate -> flatten, block short")
                except Exception:  # noqa: BLE001
                    pass
                self._committed_side = 0
                self._emit_event(ctx, "MFP_FLAT", {
                    "ts": ts, "target": int(target), "prev_side": int(cur),
                    "committed_side": 0,
                    "long_legs": long_count, "short_legs": short_count,
                    "reason": "vol_gate_block_short",
                })
            return
        super()._reconcile(ctx, target, long_count, short_count, ts)
