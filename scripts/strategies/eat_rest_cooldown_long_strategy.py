"""밥먹고 똥싸고 쉬는 전략 - RSI 과매도 반등 + 시간대/쉬는시간(cooldown).

- 밥먹는 시간: trade_hour_start~trade_hour_end(UTC)에만 거래
- 쉬는 시간: 진입/청산 후 cooldown_candles 동안 추가 진입 금지
- RSI 30 이하 과매도 반등 시 롱, 70 도달 시 청산
"""

from __future__ import annotations

import importlib
import math
from datetime import datetime, timezone
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


def _last_non_nan(values: Any) -> float | None:
    try:
        n = int(getattr(values, "size", len(values)))
    except Exception:
        return None
    for i in range(n - 1, -1, -1):
        try:
            v = float(values[i])
        except Exception:
            continue
        if not math.isnan(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, name: str) -> None:
    try:
        import numpy as np
        abstract = importlib.import_module("talib.abstract")
    except Exception:
        return

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        output_index = kwargs.pop("output_index", None)

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
            values: list[float] = []
            for series in series_list:
                v = _last_non_nan(series)
                values.append(float(v) if v is not None else math.nan)
            names = (
                ["macd", "macdsignal", "macdhist"][: len(values)]
                if normalized == "MACD"
                else [f"output_{i}" for i in range(len(values))]
            )
            if output is not None:
                try:
                    return values[names.index(str(output))]
                except ValueError:
                    return math.nan
            if output_index is not None:
                idx = int(output_index)
                return values[idx] if 0 <= idx < len(values) else math.nan
            return {names[i]: values[i] for i in range(len(values))}

        v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


def crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current


class EatRestCooldownLongStrategy(Strategy):
    """밥먹고 똥싸고 쉬는 전략 - RSI 과매도 반등 + 시간대/cooldown."""

    INDICATOR_NAME = "RSI"

    def __init__(
        self,
        period: int = 14,
        oversold_level: float = 30.0,
        exit_level: float = 70.0,
        trade_hour_start: int = 0,
        trade_hour_end: int = 24,
        cooldown_candles: int = 30,
        interval_seconds: int = 60,
    ) -> None:
        super().__init__()
        if period <= 1:
            raise ValueError("period must be > 1")
        if cooldown_candles < 0:
            raise ValueError("cooldown_candles must be >= 0")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")

        self.period = int(period)
        self.oversold_level = float(oversold_level)
        self.exit_level = float(exit_level)
        self.trade_hour_start = trade_hour_start % 24
        self.trade_hour_end = trade_hour_end % 24
        self.cooldown_candles = cooldown_candles
        self.interval_seconds = interval_seconds

        self.prev_value: float | None = None
        self.is_closing: bool = False
        self._cooldown_until_bar_ts: int | None = None

        self.params = {
            "period": self.period,
            "oversold_level": self.oversold_level,
            "exit_level": self.exit_level,
            "trade_hour_start": self.trade_hour_start,
            "trade_hour_end": self.trade_hour_end,
            "cooldown_candles": self.cooldown_candles,
            "interval_seconds": self.interval_seconds,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {"period": self.period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, self.INDICATOR_NAME)
        self.prev_value = None
        self.is_closing = False
        self._cooldown_until_bar_ts = None

    def _is_trade_hour(self, bar_ts_ms: int) -> bool:
        dt = datetime.fromtimestamp(bar_ts_ms / 1000.0, tz=timezone.utc)
        h = dt.hour
        if self.trade_hour_start < self.trade_hour_end:
            return self.trade_hour_start <= h < self.trade_hour_end
        return h >= self.trade_hour_start or h < self.trade_hour_end

    def _is_in_cooldown(self, bar_ts_ms: int) -> bool:
        if self._cooldown_until_bar_ts is None or self.cooldown_candles == 0:
            return False
        return bar_ts_ms < self._cooldown_until_bar_ts

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        bar_ts = int(bar.get("bar_timestamp", bar.get("timestamp", 0)) or 0)
        if not self._is_trade_hour(bar_ts):
            return

        value = float(ctx.get_indicator(self.INDICATOR_NAME, period=self.period))

        if not math.isfinite(value):
            return

        if self.prev_value is None or not math.isfinite(self.prev_value):
            self.prev_value = value
            return

        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_value, value, self.exit_level):
                self.is_closing = True
                ctx.close_position(reason=f"Exit Long (RSI {self.prev_value:.2f} -> {value:.2f})")
                self._cooldown_until_bar_ts = bar_ts + self.cooldown_candles * self.interval_seconds * 1000
                self.prev_value = value
                return

        if ctx.position_size == 0 and not self._is_in_cooldown(bar_ts):
            if self.prev_value <= self.oversold_level and value > self.oversold_level:
                ctx.enter_long(reason=f"Entry Long (RSI bounce {self.prev_value:.2f} -> {value:.2f})")
                self._cooldown_until_bar_ts = bar_ts + self.cooldown_candles * self.interval_seconds * 1000

        self.prev_value = value
