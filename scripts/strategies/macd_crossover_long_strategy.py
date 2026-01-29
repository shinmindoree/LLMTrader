"""MACD 크로스오버 롱 전략 - 1분봉 초단타.

MACD가 시그널을 상향 돌파할 때 롱 진입, 하향 돌파 시 청산.
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


class MacdCrossoverLongStrategy(Strategy):
    """MACD 크로스오버 롱 전략 - MACD가 시그널을 상향 돌파 시 진입."""

    def __init__(
        self,
        fastperiod: int = 12,
        slowperiod: int = 26,
        signalperiod: int = 9,
    ) -> None:
        super().__init__()
        self.fastperiod = int(fastperiod)
        self.slowperiod = int(slowperiod)
        self.signalperiod = int(signalperiod)

        self.prev_macd: float | None = None
        self.prev_signal: float | None = None
        self.is_closing: bool = False

        self.params = {
            "fastperiod": self.fastperiod,
            "slowperiod": self.slowperiod,
            "signalperiod": self.signalperiod,
        }
        self.indicator_config = {
            "MACD": {
                "fastperiod": self.fastperiod,
                "slowperiod": self.slowperiod,
                "signalperiod": self.signalperiod,
            },
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "MACD")
        self.prev_macd = None
        self.prev_signal = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        macd_result = ctx.get_indicator(
            "MACD",
            fastperiod=self.fastperiod,
            slowperiod=self.slowperiod,
            signalperiod=self.signalperiod,
        )

        if isinstance(macd_result, dict):
            macd = float(macd_result.get("macd", math.nan))
            signal = float(macd_result.get("macdsignal", math.nan))
        else:
            return

        if not math.isfinite(macd) or not math.isfinite(signal):
            return

        if self.prev_macd is None or self.prev_signal is None:
            self.prev_macd = macd
            self.prev_signal = signal
            return

        if ctx.position_size > 0 and not self.is_closing:
            if self.prev_macd > self.prev_signal and macd <= signal:
                self.is_closing = True
                ctx.close_position(reason=f"Exit Long (MACD cross down)")
                self.prev_macd = macd
                self.prev_signal = signal
                return

        if ctx.position_size == 0:
            if self.prev_macd <= self.prev_signal and macd > signal:
                ctx.enter_long(reason=f"Entry Long (MACD cross up)")

        self.prev_macd = macd
        self.prev_signal = signal
