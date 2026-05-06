"""Donchian channel breakout LONG/SHORT strategy.

Discovered by ``scripts/_alpha_lab/multi_symbol_sweep.py`` after rebuilding
the BTC/ETH/SOL klines from the authoritative Binance fapi (the previous
``BTCUSDT_15m_klines.parquet`` was corrupt; see commit ``ca33163``).

The vectorized sweep (clean fapi data, 2023-04..2026-04 train, 2025-05..
2026-04 OOS, 6bp commission + 2bp slippage) found three robust survivors
that both clear realistic friction AND produce non-trivial walk-forward
edge. They share the *same* signal logic — Donchian channel breakout with
TP/SL/max-hold exits — only the channel period and exit thresholds vary.

Recommended deployment: run all three in parallel (1/3 capital each) for
a triple-symbol/timeframe portfolio that produced +24.30% / 1.55 trades
per day on the OOS year at 6bp/2bp friction with PF 1.20 average.

Vectorized OOS metrics (2025-05 ~ 2026-04, fapi data, 6bp/2bp friction):
  BTC-2h dc=25 TP=2%/SL=2%:    +20.7% / 174 trades / PF 1.15 / DD 14.8%
  ETH-4h dc=40 TP=5%/SL=1%:    +23.1% /  83 trades / PF 1.33 / DD 11.7%
  ETH-30m dc=60 TP=5%/SL=1%:   +29.1% / 307 trades / PF 1.11 / DD 20.7%
  triple portfolio (1/3 each): +24.3% / 564 trades / 1.55 trades/day

Signal (no look-ahead, edge-triggered):
  Long:   close > max(high[-dc_period:-1])    (close breaks N-bar prior high)
  Short:  close < min(low[-dc_period:-1])     (close breaks N-bar prior low)

Exits (matches the vectorized lab semantics):
  TP:        +tp_pct from entry, fills exactly at the TP level (limit-style)
  SL:        -sl_pct from entry; if open gaps past SL fill at open, else at SL level
  Time:      after max_hold_bars from entry (close at this bar's close)

Each entry is rising-edge: at most one trade per consecutive signal block.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strategy.base import Strategy
from strategy.context import StrategyContext


# ---------------------------------------------------------------------------
# Default params — generic; override on instantiation per timeframe / symbol.
# Recommended preset constants below for the three sweep winners.
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    "dc_period": 40,        # Donchian lookback in BARS (interval-agnostic)
    "tp_pct": 0.050,
    "sl_pct": 0.010,
    "max_hold_bars": 12,    # bars (interval-agnostic)
    "entry_pct": None,
}


STRATEGY_PARAM_SCHEMA: list[dict[str, Any]] = [
    {"name": "dc_period", "type": "int", "min": 5, "max": 200,
     "label": "Donchian period (bars)"},
    {"name": "tp_pct", "type": "float", "min": 0.001, "max": 0.20, "step": 0.001,
     "label": "Take profit %"},
    {"name": "sl_pct", "type": "float", "min": 0.001, "max": 0.10, "step": 0.001,
     "label": "Stop loss %"},
    {"name": "max_hold_bars", "type": "int", "min": 1, "max": 1000,
     "label": "Max hold (bars)"},
]


# Preset configs (same dictionary key as STRATEGY_PARAMS so they can be
# spread directly into the constructor: ``DonchianBreakoutStrategy(**PRESETS["BTC-2h"])``)
PRESETS: dict[str, dict[str, Any]] = {
    # BTC 2h: dc=25, TP=2%/SL=2%, hold=48h = 24 2h-bars
    "BTC-2h":  {"dc_period": 25, "tp_pct": 0.020, "sl_pct": 0.020, "max_hold_bars": 24},
    # ETH 4h: dc=40, TP=5%/SL=1%, hold=48h = 12 4h-bars
    "ETH-4h":  {"dc_period": 40, "tp_pct": 0.050, "sl_pct": 0.010, "max_hold_bars": 12},
    # ETH 30m: dc=60, TP=5%/SL=1%, hold=48h = 96 30m-bars
    "ETH-30m": {"dc_period": 60, "tp_pct": 0.050, "sl_pct": 0.010, "max_hold_bars": 96},
}


class DonchianBreakoutStrategy(Strategy):
    """Donchian channel breakout (LONG + SHORT) with TP/SL/time exit."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.dc_period = int(p["dc_period"])
        self.tp_pct = float(p["tp_pct"])
        self.sl_pct = float(p["sl_pct"])
        self.max_hold_bars = int(p["max_hold_bars"])
        self.entry_pct = p["entry_pct"]

        self._mode: str | None = None
        self._entry_bar_index: int | None = None
        self._entry_price: float | None = None
        self._bar_index: int = 0
        self._is_closing: bool = False
        self._prev_long_signal: bool = False
        self._prev_short_signal: bool = False
        self._eval_log_every_bars: int = 1
        self._last_eval_log_bar: int = -10**9

        self.params = dict(p)
        # No TA-Lib indicators required; we register a custom Donchian fn below.
        self.indicator_config = {}

    # ------------------------------------------------------------------ init
    def initialize(self, ctx: StrategyContext) -> None:
        ctx_cls = type(ctx).__name__
        ctx_module = type(ctx).__module__
        if "Backtest" in ctx_cls:
            mode = "backtest"
        elif (
            "Live" in ctx_cls
            or ctx_cls == "StreamBoundStrategyContext"
            or ctx_module.startswith("live.")
        ):
            mode = "live"
        else:
            mode = None
        self._mode = mode

        # Custom Donchian channel that reads the engine's high/low arrays.
        # We must EXCLUDE the current bar (shift(1)) to match the vectorized
        # backtester used during the sweep.
        period = self.dc_period

        def _donchian(inner_ctx: Any) -> dict[str, float]:
            inputs_fn = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
            if not callable(inputs_fn):
                return {"upper": math.nan, "lower": math.nan}
            raw = inputs_fn()
            h = raw.get("high")
            lo = raw.get("low")
            if h is None or lo is None:
                return {"upper": math.nan, "lower": math.nan}
            h_arr = np.asarray(list(h), dtype="float64") if not hasattr(h, "dtype") else h
            lo_arr = np.asarray(list(lo), dtype="float64") if not hasattr(lo, "dtype") else lo
            n = len(h_arr)
            if n < period + 1:
                return {"upper": math.nan, "lower": math.nan}
            # PRIOR window: indices [n-1-period .. n-2] (exclusive of current)
            upper = float(np.max(h_arr[n - 1 - period:n - 1]))
            lower = float(np.min(lo_arr[n - 1 - period:n - 1]))
            return {"upper": upper, "lower": lower}

        ctx.register_indicator("DONCHIAN", _donchian)

        self._entry_bar_index = None
        self._entry_price = None
        self._bar_index = 0
        self._is_closing = False
        self._prev_long_signal = False
        self._prev_short_signal = False
        self._last_eval_log_bar = -10**9

        symbol = getattr(ctx, "symbol", "?")
        self._emit_event(ctx, "DONCHIAN_INIT", {
            "symbol": symbol, "mode": mode,
            "dc_period": self.dc_period,
            "tp_pct": self.tp_pct, "sl_pct": self.sl_pct,
            "max_hold_bars": self.max_hold_bars,
        })

    # ------------------------------------------------------------------ bar
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self._is_closing = False
            self._entry_bar_index = None
            self._entry_price = None

        is_new_bar = bool(bar.get("is_new_bar", True))
        # Mirror vectorized backtester semantics: act only on new-bar close ticks.
        if not is_new_bar:
            return

        close = float(bar.get("close", bar.get("price", 0.0)) or 0.0)
        if not math.isfinite(close) or close <= 0:
            return
        open_ = float(bar.get("open", close) or close)
        high = float(bar.get("high", close) or close)
        low = float(bar.get("low", close) or close)

        # ---- Exits against this bar's full OHLC -------------------------
        if (ctx.position_size != 0 and self._entry_price is not None
                and not self._is_closing):
            if ctx.position_size > 0:  # LONG
                tp_level = self._entry_price * (1.0 + self.tp_pct)
                sl_level = self._entry_price * (1.0 - self.sl_pct)
                if open_ <= sl_level:
                    self._is_closing = True
                    sl_fill = open_
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_fill, reason=f"DC: SL_GAP -{self.sl_pct * 100:.1f}%")
                    else:
                        ctx.close_position(reason=f"DC: SL_GAP -{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "DONCHIAN_EXIT_SL_LONG", {
                        "entry_price": self._entry_price, "exit_price": sl_fill,
                        "sl_level": sl_level, "kind": "GAP"})
                    return
                if low <= sl_level:
                    self._is_closing = True
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_level, reason=f"DC: SL -{self.sl_pct * 100:.1f}%")
                    else:
                        ctx.close_position(reason=f"DC: SL -{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "DONCHIAN_EXIT_SL_LONG", {
                        "entry_price": self._entry_price, "exit_price": sl_level,
                        "sl_level": sl_level, "kind": "TOUCH"})
                    return
                if high >= tp_level:
                    self._is_closing = True
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            tp_level, reason=f"DC: TP +{self.tp_pct * 100:.1f}%")
                    else:
                        ctx.close_position(reason=f"DC: TP +{self.tp_pct * 100:.1f}%")
                    self._emit_event(ctx, "DONCHIAN_EXIT_TP_LONG", {
                        "entry_price": self._entry_price, "exit_price": tp_level,
                        "tp_level": tp_level})
                    return
            else:  # SHORT
                tp_level = self._entry_price * (1.0 - self.tp_pct)
                sl_level = self._entry_price * (1.0 + self.sl_pct)
                if open_ >= sl_level:
                    self._is_closing = True
                    sl_fill = open_
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_fill, reason=f"DC: SL_GAP +{self.sl_pct * 100:.1f}%")
                    else:
                        ctx.close_position(reason=f"DC: SL_GAP +{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "DONCHIAN_EXIT_SL_SHORT", {
                        "entry_price": self._entry_price, "exit_price": sl_fill,
                        "sl_level": sl_level, "kind": "GAP"})
                    return
                if high >= sl_level:
                    self._is_closing = True
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            sl_level, reason=f"DC: SL +{self.sl_pct * 100:.1f}%")
                    else:
                        ctx.close_position(reason=f"DC: SL +{self.sl_pct * 100:.1f}%")
                    self._emit_event(ctx, "DONCHIAN_EXIT_SL_SHORT", {
                        "entry_price": self._entry_price, "exit_price": sl_level,
                        "sl_level": sl_level, "kind": "TOUCH"})
                    return
                if low <= tp_level:
                    self._is_closing = True
                    if self._mode == "backtest" and hasattr(ctx, "close_position_at_price"):
                        ctx.close_position_at_price(
                            tp_level, reason=f"DC: TP +{self.tp_pct * 100:.1f}%")
                    else:
                        ctx.close_position(reason=f"DC: TP +{self.tp_pct * 100:.1f}%")
                    self._emit_event(ctx, "DONCHIAN_EXIT_TP_SHORT", {
                        "entry_price": self._entry_price, "exit_price": tp_level,
                        "tp_level": tp_level})
                    return
            # Time exit: close at this bar's close after max_hold_bars.
            if (self._entry_bar_index is not None
                    and self._bar_index - self._entry_bar_index >= self.max_hold_bars):
                self._is_closing = True
                ctx.close_position(reason=f"DC: time exit ({self.max_hold_bars} bars)")
                self._emit_event(ctx, "DONCHIAN_EXIT_TIME", {
                    "entry_price": self._entry_price, "exit_price": close,
                    "held_bars": self._bar_index - self._entry_bar_index})
                return

        # ---- Entry: only on new bar close, no open orders, flat -----------
        try:
            open_orders = ctx.get_open_orders() or []
        except Exception:  # noqa: BLE001
            open_orders = []
        if open_orders:
            return

        self._bar_index += 1
        if ctx.position_size != 0:
            return

        dc = ctx.get_indicator("DONCHIAN")
        if not isinstance(dc, dict):
            return
        upper = float(dc.get("upper", math.nan))
        lower = float(dc.get("lower", math.nan))
        if not (math.isfinite(upper) and math.isfinite(lower)):
            return

        long_signal = close > upper
        short_signal = close < lower

        if (self._bar_index - self._last_eval_log_bar) >= max(1, self._eval_log_every_bars):
            self._emit_event(ctx, "DONCHIAN_SIGNAL_EVAL", {
                "close": close, "upper": upper, "lower": lower,
                "long_signal": long_signal, "short_signal": short_signal,
                "long_edge": long_signal and not self._prev_long_signal,
                "short_edge": short_signal and not self._prev_short_signal,
            })
            self._last_eval_log_bar = self._bar_index

        if long_signal and not self._prev_long_signal:
            reason = (f"Donchian long: c={close:.2f} > U={upper:.2f} "
                      f"(dc={self.dc_period})")
            if self.entry_pct is None:
                ctx.enter_long(reason=reason)
            else:
                ctx.enter_long(reason=reason, entry_pct=float(self.entry_pct))
            self._entry_bar_index = self._bar_index
            self._entry_price = close
            self._emit_event(ctx, "DONCHIAN_ENTRY_LONG", {
                "entry_price": close, "upper": upper,
                "tp_level": round(close * (1.0 + self.tp_pct), 4),
                "sl_level": round(close * (1.0 - self.sl_pct), 4),
            })
        elif short_signal and not self._prev_short_signal:
            reason = (f"Donchian short: c={close:.2f} < L={lower:.2f} "
                      f"(dc={self.dc_period})")
            if self.entry_pct is None:
                ctx.enter_short(reason=reason)
            else:
                ctx.enter_short(reason=reason, entry_pct=float(self.entry_pct))
            self._entry_bar_index = self._bar_index
            self._entry_price = close
            self._emit_event(ctx, "DONCHIAN_ENTRY_SHORT", {
                "entry_price": close, "lower": lower,
                "tp_level": round(close * (1.0 - self.tp_pct), 4),
                "sl_level": round(close * (1.0 + self.sl_pct), 4),
            })

        self._prev_long_signal = long_signal
        self._prev_short_signal = short_signal

    # ---- helpers -----------------------------------------------------------
    def _emit_event(self, ctx: Any, action: str, data: dict[str, Any]) -> None:
        fn = getattr(ctx, "log_event", None)
        if not callable(fn):
            return
        try:
            fn(action, data)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Convenience preset wrappers — for direct selection in the UI / runner.
# ---------------------------------------------------------------------------
class DonchianBreakoutBtc2hStrategy(DonchianBreakoutStrategy):
    """Preset: BTC 2h donchian dc=25, TP=2%/SL=2%, hold=48h (24 bars)."""

    def __init__(self, **kwargs: Any) -> None:
        merged = {**PRESETS["BTC-2h"], **kwargs}
        super().__init__(**merged)


class DonchianBreakoutEth4hStrategy(DonchianBreakoutStrategy):
    """Preset: ETH 4h donchian dc=40, TP=5%/SL=1%, hold=48h (12 bars)."""

    def __init__(self, **kwargs: Any) -> None:
        merged = {**PRESETS["ETH-4h"], **kwargs}
        super().__init__(**merged)


class DonchianBreakoutEth30mStrategy(DonchianBreakoutStrategy):
    """Preset: ETH 30m donchian dc=60, TP=5%/SL=1%, hold=48h (96 bars)."""

    def __init__(self, **kwargs: Any) -> None:
        merged = {**PRESETS["ETH-30m"], **kwargs}
        super().__init__(**merged)
