"""BTC ATR-trend confluence long strategy (15m).

Production strategy derived from a 69k-combo sweep on BTCUSDT futures 2026-03-30..04-29.
Best parameters:
  rsi_os=30, wr_os=-70, ema_trend=50, atr_tp_mult=4.0, atr_sl_mult=1.0, max_hold=20,
  use_cdl=1.
Entry: any of {RSI cross-up over 30, Williams %R cross-up over -70, Stoch K cross-up over
20, MACD cross-up, bullish CDL pattern} on a confirmed bar AND price > EMA(50).
Exit: ATR-based TP (4x ATR above entry) and SL (1x ATR below) checked intra-bar against
the bar's high/low; or time exit at max_hold (20) bars.

Backtest result (fast harness, fee 0.04%/side): 5.45 trades/day, 32.0% win, +7.41% return.
"""
from __future__ import annotations

import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


CDL_PATTERNS = ("CDLHAMMER", "CDLENGULFING", "CDLPIERCING", "CDLMORNINGSTAR",
                "CDLINVERTEDHAMMER", "CDLDRAGONFLYDOJI", "CDL3WHITESOLDIERS",
                "CDLBELTHOLD", "CDL3INSIDE")


STRATEGY_PARAMS: dict[str, Any] = {
    "rsi_period": 14,
    "rsi_os": 30.0,
    "wr_period": 14,
    "wr_os": -70.0,
    "stoch_fastk_period": 14,
    "stoch_slowk_period": 3,
    "stoch_slowd_period": 3,
    "stoch_os": 20.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "atr_period": 14,
    "ema_trend_period": 50,
    "use_rsi": 1,
    "use_wr": 1,
    "use_stoch": 1,
    "use_macd": 1,
    "use_cdl": 1,
    "use_trend_filter": 1,
    "atr_tp_multiplier": 4.0,
    "atr_sl_multiplier": 1.0,
    "max_hold_bars": 20,
    "cooldown_bars": 1,
}


class AtrTrendConfluenceLongStrategy(Strategy):
    """Multi-trigger oversold-bounce + bullish-pattern long with ATR-based exits."""

    def __init__(self, **params: Any) -> None:
        super().__init__()
        merged = dict(STRATEGY_PARAMS)
        merged.update(params)
        for k, v in merged.items():
            setattr(self, k, v)
        self.params = dict(merged)
        self.indicator_config: dict[str, dict[str, Any]] = {}

        self.prev_rsi: float | None = None
        self.prev_wr: float | None = None
        self.prev_stoch_k: float | None = None
        self.prev_macd_line: float | None = None
        self.prev_macd_signal: float | None = None
        self.bars_since_entry: int = 0
        self.bars_since_exit: int = 10**9
        self.entry_price: float | None = None
        self.tp_price: float | None = None
        self.sl_price: float | None = None
        self.is_closing: bool = False

    def initialize(self, ctx: StrategyContext) -> None:
        # Use builtin (cached) indicator path — do NOT register custom wrappers.
        self.prev_rsi = None
        self.prev_wr = None
        self.prev_stoch_k = None
        self.prev_macd_line = None
        self.prev_macd_signal = None
        self.bars_since_entry = 0
        self.bars_since_exit = 10**9
        self.entry_price = None
        self.tp_price = None
        self.sl_price = None
        self.is_closing = False

    def _check_intrabar_exit(self, ctx: StrategyContext, bar: dict[str, Any]) -> bool:
        if ctx.position_size <= 0 or self.is_closing:
            return False
        if self.tp_price is None or self.sl_price is None:
            return False
        high = float(bar.get("high", ctx.current_price))
        low = float(bar.get("low", ctx.current_price))
        # SL has priority (worst-case fill assumption). Use close_position_at_price to
        # fill exactly at the trigger level (not whatever ctx.current_price happens to be).
        if low <= self.sl_price:
            self.is_closing = True
            ctx.close_position_at_price(self.sl_price, reason=f"SL hit @{self.sl_price:.2f}")
            self.bars_since_exit = 0
            return True
        if high >= self.tp_price:
            self.is_closing = True
            ctx.close_position_at_price(self.tp_price, reason=f"TP hit @{self.tp_price:.2f}")
            self.bars_since_exit = 0
            return True
        return False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False
            self.entry_price = None
            self.tp_price = None
            self.sl_price = None

        if ctx.get_open_orders():
            return

        is_new_bar = bool(bar.get("is_new_bar", True))

        # Skip intra-bar sub-events; we handle TP/SL using the bar's full range on
        # the new-bar tick, which matches the precomputed-indicator backtest harness.
        if not is_new_bar:
            return

        # New bar.
        if ctx.position_size > 0 and not self.is_closing:
            self.bars_since_entry += 1
            # Check TP/SL using this bar's range.
            if self._check_intrabar_exit(ctx, bar):
                return
            if self.bars_since_entry >= int(self.max_hold_bars):
                self.is_closing = True
                ctx.close_position(reason=f"Time exit {self.bars_since_entry}")
                self.bars_since_exit = 0
                return

        price = float(ctx.current_price)

        rsi = float(ctx.get_indicator("RSI", period=int(self.rsi_period)))
        wr = float(ctx.get_indicator("WILLR", period=int(self.wr_period)))
        stoch_k = float(ctx.get_indicator(
            "STOCH",
            fastk_period=int(self.stoch_fastk_period),
            slowk_period=int(self.stoch_slowk_period),
            slowd_period=int(self.stoch_slowd_period),
            output="slowk",
        ))
        macd_line = float(ctx.get_indicator(
            "MACD",
            fastperiod=int(self.macd_fast),
            slowperiod=int(self.macd_slow),
            signalperiod=int(self.macd_signal),
            output="macd",
        ))
        macd_sig = float(ctx.get_indicator(
            "MACD",
            fastperiod=int(self.macd_fast),
            slowperiod=int(self.macd_slow),
            signalperiod=int(self.macd_signal),
            output="macdsignal",
        ))
        atr = float(ctx.get_indicator("ATR", period=int(self.atr_period)))
        ema_trend = float(ctx.get_indicator("EMA", period=int(self.ema_trend_period)))

        prev_rsi = self.prev_rsi
        prev_wr = self.prev_wr
        prev_stoch_k = self.prev_stoch_k
        prev_macd_line = self.prev_macd_line
        prev_macd_sig = self.prev_macd_signal

        triggers: list[str] = []

        if int(self.use_rsi) and prev_rsi is not None and math.isfinite(prev_rsi) and math.isfinite(rsi):
            if prev_rsi <= self.rsi_os and rsi > self.rsi_os:
                triggers.append("RSI")
        if int(self.use_wr) and prev_wr is not None and math.isfinite(prev_wr) and math.isfinite(wr):
            if prev_wr <= self.wr_os and wr > self.wr_os:
                triggers.append("WR")
        if int(self.use_stoch) and prev_stoch_k is not None and math.isfinite(prev_stoch_k) and math.isfinite(stoch_k):
            if prev_stoch_k <= self.stoch_os and stoch_k > self.stoch_os:
                triggers.append("STOCH")
        if (int(self.use_macd) and prev_macd_line is not None and prev_macd_sig is not None
                and math.isfinite(prev_macd_line) and math.isfinite(prev_macd_sig)
                and math.isfinite(macd_line) and math.isfinite(macd_sig)):
            if prev_macd_line <= prev_macd_sig and macd_line > macd_sig:
                triggers.append("MACD")
        if int(self.use_cdl):
            for nm in CDL_PATTERNS:
                try:
                    v = float(ctx.get_indicator(nm))
                except Exception:
                    v = 0.0
                if math.isfinite(v) and v > 0:
                    triggers.append(nm)
                    break

        # Update previous values for next bar.
        self.prev_rsi = rsi if math.isfinite(rsi) else self.prev_rsi
        self.prev_wr = wr if math.isfinite(wr) else self.prev_wr
        self.prev_stoch_k = stoch_k if math.isfinite(stoch_k) else self.prev_stoch_k
        self.prev_macd_line = macd_line if math.isfinite(macd_line) else self.prev_macd_line
        self.prev_macd_signal = macd_sig if math.isfinite(macd_sig) else self.prev_macd_signal

        if ctx.position_size != 0:
            return
        self.bars_since_exit += 1
        if self.bars_since_exit < int(self.cooldown_bars):
            return
        if not (math.isfinite(atr) and atr > 0):
            return
        if int(self.use_trend_filter) and math.isfinite(ema_trend):
            if price < ema_trend:
                return
        if not triggers:
            return

        tp_mult = float(self.atr_tp_multiplier)
        sl_mult = float(self.atr_sl_multiplier)
        self.entry_price = price
        self.tp_price = price + tp_mult * atr
        self.sl_price = price - sl_mult * atr
        self.bars_since_entry = 0
        ctx.enter_long(reason=f"AtrTrend[{','.join(triggers)}]")
