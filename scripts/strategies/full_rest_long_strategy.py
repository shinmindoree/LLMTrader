"""밥먹고 배부르면 쉬는 전략 - RSI 롱.

과매도(배고플 때)에서 롱 진입(밥 먹기), 과매수(배 부르면)에서 청산(쉬기).
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


class FullRestLongStrategy(Strategy):
    """밥먹고 배부르면 쉬는 전략: RSI 과매도에서 롱 진입, 과매수에서 청산."""

    def __init__(
        self,
        period: int = 14,
        hungry_level: float = 30.0,
        full_level: float = 70.0,
    ) -> None:
        super().__init__()
        if period <= 1:
            raise ValueError("period must be > 1")

        self.period = int(period)
        self.hungry_level = float(hungry_level)
        self.full_level = float(full_level)

        self.prev_value: float | None = None
        self.is_closing: bool = False

        self.params = {
            "period": self.period,
            "hungry_level": self.hungry_level,
            "full_level": self.full_level,
        }
        self.indicator_config = {
            "RSI": {"period": self.period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "RSI")
        self.prev_value = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        value = float(ctx.get_indicator("RSI", period=self.period))

        if not math.isfinite(value):
            return

        if self.prev_value is None or not math.isfinite(self.prev_value):
            self.prev_value = value
            return

        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_value, value, self.full_level):
                self.is_closing = True
                ctx.close_position(reason=f"Rest when full (RSI {self.prev_value:.2f} -> {value:.2f})")
                self.prev_value = value
                return

        if ctx.position_size == 0:
            if self.prev_value <= self.hungry_level and value > self.hungry_level:
                ctx.enter_long(reason=f"Eat (RSI {self.prev_value:.2f} -> {value:.2f})")

        self.prev_value = value
