"""EMA 크로스오버 롱 전략 - 1분봉 초단타.

빠른 EMA가 느린 EMA를 상향 돌파할 때 롱 진입, 하향 돌파 시 청산.
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


class EmaCrossoverLongStrategy(Strategy):
    """EMA 크로스오버 롱 전략 - 빠른 EMA가 느린 EMA를 상향 돌파 시 진입."""

    def __init__(
        self,
        fast_period: int = 9,
        slow_period: int = 21,
    ) -> None:
        super().__init__()
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")

        self.fast_period = int(fast_period)
        self.slow_period = int(slow_period)

        self.prev_fast: float | None = None
        self.prev_slow: float | None = None
        self.is_closing: bool = False

        self.params = {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
        }
        self.indicator_config = {
            "EMA": {"fast": self.fast_period, "slow": self.slow_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "EMA")
        self.prev_fast = None
        self.prev_slow = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        fast = float(ctx.get_indicator("EMA", period=self.fast_period))
        slow = float(ctx.get_indicator("EMA", period=self.slow_period))

        if not math.isfinite(fast) or not math.isfinite(slow):
            return

        if self.prev_fast is None or self.prev_slow is None:
            self.prev_fast = fast
            self.prev_slow = slow
            return

        if ctx.position_size > 0 and not self.is_closing:
            if self.prev_fast > self.prev_slow and fast <= slow:
                self.is_closing = True
                ctx.close_position(reason=f"Exit Long (EMA cross down)")
                self.prev_fast = fast
                self.prev_slow = slow
                return

        if ctx.position_size == 0:
            if self.prev_fast <= self.prev_slow and fast > slow:
                ctx.enter_long(reason=f"Entry Long (EMA cross up)")

        self.prev_fast = fast
        self.prev_slow = slow
