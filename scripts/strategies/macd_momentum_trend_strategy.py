"""MACD momentum strategy with EMA200 trend filter for BTCUSDT.

규칙:
- 추세: close > EMA(trend_period) → 롱만, 반대면 숏만 (use_trend_filter=True)
- 진입:
    * 롱: MACD line이 signal line을 상향 돌파 + close > EMA(trend) (또는 필터 off 시 항상)
    * 숏: MACD line이 signal line을 하향 돌파 + close < EMA(trend)
- 청산: ATR TP/SL, 시간 만료, MACD 반대 cross 시
"""

from __future__ import annotations

import importlib
import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


def _last_non_nan(values: Any) -> float | None:
    try:
        n = int(getattr(values, "size", len(values)))
    except Exception:  # noqa: BLE001
        return None
    for i in range(n - 1, -1, -1):
        try:
            v = float(values[i])
        except Exception:  # noqa: BLE001
            continue
        if not math.isnan(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, name: str) -> None:
    try:
        import numpy as np  # type: ignore
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    _OHLCV_KEYS = {"open", "high", "low", "close", "volume", "real"}

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        output_index = kwargs.pop("output_index", None)
        price_source = kwargs.pop("price", None)

        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("builtin indicator params must be passed as keywords (or single period)")

        if "period" in kwargs and "timeperiod" not in kwargs:
            kwargs["timeperiod"] = kwargs.pop("period")

        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw_inputs = inputs()
        prepared_inputs = {
            key: (np.asarray(list(values), dtype="float64") if not hasattr(values, "dtype") else values)
            for key, values in raw_inputs.items()
        }
        if "real" not in prepared_inputs and "close" in prepared_inputs:
            prepared_inputs["real"] = prepared_inputs["close"]

        if price_source is not None:
            if price_source.lower() in _OHLCV_KEYS:
                prepared_inputs["real"] = prepared_inputs.get(price_source.lower(), prepared_inputs.get("close"))

        normalized = name.strip().upper()
        fn = abstract.Function(normalized)
        result = fn(prepared_inputs, **kwargs)

        if isinstance(result, dict):
            out: dict[str, float] = {}
            for key, series in result.items():
                v = _last_non_nan(series)
                out[str(key)] = float(v) if v is not None else math.nan
            if output is not None:
                return float(out.get(str(output), math.nan))
            if output_index is not None:
                keys = list(out.keys())
                idx = int(output_index)
                return float(out.get(keys[idx], math.nan)) if 0 <= idx < len(keys) else math.nan
            return out

        if isinstance(result, (list, tuple)):
            series_list = list(result)
            values_list: list[float] = []
            for series in series_list:
                v = _last_non_nan(series)
                values_list.append(float(v) if v is not None else math.nan)
            names = [f"output_{i}" for i in range(len(values_list))]
            if output is not None:
                try:
                    return values_list[names.index(str(output))]
                except ValueError:
                    return math.nan
            if output_index is not None:
                idx = int(output_index)
                return values_list[idx] if 0 <= idx < len(values_list) else math.nan
            return {names[i]: values_list[i] for i in range(len(values_list))}

        v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


STRATEGY_PARAMS: dict[str, Any] = {
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "trend_period": 200,
    "use_trend_filter": True,
    "atr_period": 14,
    "atr_tp_multiplier": 2.0,
    "atr_sl_multiplier": 1.0,
    "max_hold_bars": 60,
    "cooldown_bars": 2,
    "exit_on_opposite_cross": True,
}


class MacdMomentumStrategy(Strategy):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.macd_fast = int(p["macd_fast"])
        self.macd_slow = int(p["macd_slow"])
        self.macd_signal = int(p["macd_signal"])
        self.trend_period = int(p["trend_period"])
        self.use_trend_filter = bool(p["use_trend_filter"])
        self.atr_period = int(p["atr_period"])
        self.atr_tp_multiplier = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.max_hold_bars = int(p["max_hold_bars"])
        self.cooldown_bars = int(p["cooldown_bars"])
        self.exit_on_opposite_cross = bool(p["exit_on_opposite_cross"])

        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self._bars_since_close: int | None = None
        self._bars_in_position: int = 0
        self._prev_macd: float | None = None
        self._prev_signal: float | None = None

        self.params = dict(p)
        self.indicator_config = {
            "MACD": {"fastperiod": self.macd_fast, "slowperiod": self.macd_slow, "signalperiod": self.macd_signal},
            "EMA": {"period": self.trend_period},
            "ATR": {"period": self.atr_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        for n in ("MACD", "EMA", "ATR"):
            register_talib_indicator_all_outputs(ctx, n)
        self.is_closing = False
        self.tp_price = 0.0
        self.sl_price = 0.0
        self._bars_since_close = None
        self._bars_in_position = 0
        self._prev_macd = None
        self._prev_signal = None

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False
            self._bars_in_position = 0

        if ctx.get_open_orders():
            return

        # ATR exits
        if ctx.position_size != 0 and not self.is_closing:
            price = ctx.current_price
            if ctx.position_size > 0:
                if price >= self.tp_price:
                    self.is_closing = True; self._bars_since_close = 0
                    ctx.close_position(reason=f"TP Long {price:.2f}", exit_reason="TAKE_PROFIT"); return
                if price <= self.sl_price:
                    self.is_closing = True; self._bars_since_close = 0
                    ctx.close_position(reason=f"SL Long {price:.2f}", exit_reason="STOP_LOSS"); return
            else:
                if price <= self.tp_price:
                    self.is_closing = True; self._bars_since_close = 0
                    ctx.close_position(reason=f"TP Short {price:.2f}", exit_reason="TAKE_PROFIT"); return
                if price >= self.sl_price:
                    self.is_closing = True; self._bars_since_close = 0
                    ctx.close_position(reason=f"SL Short {price:.2f}", exit_reason="STOP_LOSS"); return

        if not bool(bar.get("is_new_bar", True)):
            return

        if self._bars_since_close is not None:
            self._bars_since_close += 1

        macd_dict = ctx.get_indicator(
            "MACD",
            fastperiod=self.macd_fast,
            slowperiod=self.macd_slow,
            signalperiod=self.macd_signal,
        )
        if not isinstance(macd_dict, dict):
            return
        macd = float(macd_dict.get("macd", macd_dict.get("output_0", math.nan)))
        signal = float(macd_dict.get("macdsignal", macd_dict.get("output_1", math.nan)))
        ema_trend = float(ctx.get_indicator("EMA", period=self.trend_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        price = ctx.current_price

        if not all(math.isfinite(v) for v in (macd, signal, ema_trend, atr, price)) or atr <= 0:
            return

        prev_macd = self._prev_macd
        prev_signal = self._prev_signal
        self._prev_macd = macd
        self._prev_signal = signal

        # Hold — opposite cross exit
        if ctx.position_size != 0:
            self._bars_in_position += 1
            if not self.is_closing and self.exit_on_opposite_cross and prev_macd is not None and prev_signal is not None:
                if ctx.position_size > 0 and prev_macd >= prev_signal and macd < signal:
                    self.is_closing = True; self._bars_since_close = 0
                    ctx.close_position(reason="MACD bear cross", exit_reason="SIGNAL_EXIT"); return
                if ctx.position_size < 0 and prev_macd <= prev_signal and macd > signal:
                    self.is_closing = True; self._bars_since_close = 0
                    ctx.close_position(reason="MACD bull cross", exit_reason="SIGNAL_EXIT"); return
            if self.max_hold_bars > 0 and self._bars_in_position >= self.max_hold_bars and not self.is_closing:
                self.is_closing = True; self._bars_since_close = 0
                ctx.close_position(reason=f"Time exit {self._bars_in_position}", exit_reason="TIME_EXIT")
            return

        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            return
        if prev_macd is None or prev_signal is None:
            return

        bull_cross = prev_macd <= prev_signal and macd > signal
        bear_cross = prev_macd >= prev_signal and macd < signal

        # Long: bull cross + (no filter or above EMA200)
        if bull_cross and (not self.use_trend_filter or price > ema_trend):
            self.tp_price = price + self.atr_tp_multiplier * atr
            self.sl_price = price - self.atr_sl_multiplier * atr
            self._bars_since_close = None; self._bars_in_position = 0
            ctx.enter_long(
                reason=f"MACD bull (M={macd:.4f}>S={signal:.4f}, EMA200={ema_trend:.2f}) TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
            )
            return

        if bear_cross and (not self.use_trend_filter or price < ema_trend):
            self.tp_price = price - self.atr_tp_multiplier * atr
            self.sl_price = price + self.atr_sl_multiplier * atr
            self._bars_since_close = None; self._bars_in_position = 0
            ctx.enter_short(
                reason=f"MACD bear (M={macd:.4f}<S={signal:.4f}, EMA200={ema_trend:.2f}) TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
            )
            return
