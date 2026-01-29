"""Stochastic 과매도 반등 롱 전략 - 1분봉 초단타.

Stochastic %K가 20 이하에서 상승 반등할 때 롱 진입, 80 이상 도달 시 청산.
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


class StochasticOversoldBounceLongStrategy(Strategy):
    """Stochastic 과매도 반등 롱 전략 - %K가 20 이하에서 상승 반등 시 진입."""

    def __init__(
        self,
        fastk_period: int = 14,
        slowk_period: int = 3,
        slowd_period: int = 3,
        oversold_level: float = 20.0,
        exit_level: float = 80.0,
    ) -> None:
        super().__init__()
        if fastk_period <= 1:
            raise ValueError("fastk_period must be > 1")

        self.fastk_period = int(fastk_period)
        self.slowk_period = int(slowk_period)
        self.slowd_period = int(slowd_period)
        self.oversold_level = float(oversold_level)
        self.exit_level = float(exit_level)

        self.prev_k: float | None = None
        self.is_closing: bool = False

        self.params = {
            "fastk_period": self.fastk_period,
            "slowk_period": self.slowk_period,
            "slowd_period": self.slowd_period,
            "oversold_level": self.oversold_level,
            "exit_level": self.exit_level,
        }
        self.indicator_config = {
            "STOCH": {
                "fastk_period": self.fastk_period,
                "slowk_period": self.slowk_period,
                "slowd_period": self.slowd_period,
            },
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "STOCH")
        self.prev_k = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        stoch = ctx.get_indicator(
            "STOCH",
            fastk_period=self.fastk_period,
            slowk_period=self.slowk_period,
            slowd_period=self.slowd_period,
        )

        if isinstance(stoch, dict):
            k = float(stoch.get("slowk", math.nan))
        else:
            return

        if not math.isfinite(k):
            return

        if self.prev_k is None or not math.isfinite(self.prev_k):
            self.prev_k = k
            return

        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_k, k, self.exit_level):
                self.is_closing = True
                ctx.close_position(reason=f"Exit Long (Stoch {self.prev_k:.2f} -> {k:.2f})")
                self.prev_k = k
                return

        if ctx.position_size == 0:
            if self.prev_k <= self.oversold_level and k > self.oversold_level:
                ctx.enter_long(reason=f"Entry Long (Stoch bounce {self.prev_k:.2f} -> {k:.2f})")

        self.prev_k = k
