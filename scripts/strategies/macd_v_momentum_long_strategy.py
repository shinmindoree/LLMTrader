"""MACD-V (Volatility-Normalized MACD) 기반 모멘텀 롱 전략.

Alex Spiroglou (CMT Charles Dao Award & NAAIM Founders Award, 2022)의
Award-Winning Momentum Indicator를 적용한 전략.

핵심 공식: MACD-V = (EMA(12) - EMA(26)) / ATR(26) × 100
- 시간·자산에 관계없이 비교 가능한 모멘텀 오실레이터
- ±150: 극단/과도 구간 (반전 리스크)
- +50 ~ +150 / -50 ~ -150: 강한 방향성 모멘텀
- -50 ~ +50: 중립/휩쏘 구간 (신호 회피)

전략:
- 롱 진입: MACD-V가 과매도(-50 ~ -150)에서 -50 상향 돌파 (강한 반등 구간)
- 롱 청산: MACD-V가 +150 상향 돌파(극단 이익 실현) 또는 +50 하향 돌파(모멘텀 약화)
- 선택: 200 EMA 상단(bullish regime)에서만 진입
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


def register_macdv(ctx: StrategyContext) -> None:
    """MACD-V = (EMA(12) - EMA(26)) / ATR(26) × 100 를 등록."""

    try:
        import numpy as np  # type: ignore
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    def _macdv(inner_ctx: Any, *args: Any, **kwargs: Any) -> float:
        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw = inputs()
        close = np.asarray(list(raw.get("close", [])), dtype="float64")
        high = np.asarray(list(raw.get("high", [])), dtype="float64")
        low = np.asarray(list(raw.get("low", [])), dtype="float64")
        if close.size < 26:
            return float("nan")
        ema12 = abstract.Function("EMA")({"close": close}, timeperiod=12)
        ema26 = abstract.Function("EMA")({"close": close}, timeperiod=26)
        atr26 = abstract.Function("ATR")({"high": high, "low": low, "close": close}, timeperiod=26)
        diff = ema12 - ema26
        atr_last = _last_non_nan(atr26)
        if atr_last is None or atr_last <= 0 or not math.isfinite(atr_last):
            return float("nan")
        v = _last_non_nan(diff)
        if v is None or not math.isfinite(v):
            return float("nan")
        return float(v / atr_last * 100.0)

    ctx.register_indicator("MACDV", _macdv)


def crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    return current <= level < prev


class MacdVMomentumLongStrategy(Strategy):
    """MACD-V 기반 모멘텀 롱 전략.

    - 진입: MACD-V가 과매도(-50 ~ -150)에서 -50 상향 돌파 시
    - 청산: +150 상향 돌파(극단 이익 실현) 또는 +50 하향 돌파(모멘텀 약화)
    - 선택: use_ema200_filter=True 시 가격 > EMA(200)일 때만 진입 (bullish regime)
    """

    def __init__(
        self,
        oversold_level: float = -50.0,
        take_profit_level: float = 150.0,
        momentum_exit_level: float = 50.0,
        use_ema200_filter: bool = True,
    ) -> None:
        super().__init__()
        self.oversold_level = float(oversold_level)
        self.take_profit_level = float(take_profit_level)
        self.momentum_exit_level = float(momentum_exit_level)
        self.use_ema200_filter = bool(use_ema200_filter)

        self.prev_macdv: float | None = None
        self.is_closing: bool = False

        self.params = {
            "oversold_level": self.oversold_level,
            "take_profit_level": self.take_profit_level,
            "momentum_exit_level": self.momentum_exit_level,
            "use_ema200_filter": self.use_ema200_filter,
        }
        self.indicator_config = {
            "MACDV": {"fast": 12, "slow": 26, "atr_period": 26},
            "EMA": {"period": 200},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_macdv(ctx)
        register_talib_indicator_all_outputs(ctx, "EMA")
        self.prev_macdv = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        if ctx.get_open_orders():
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        macdv = ctx.get_indicator("MACDV")
        if isinstance(macdv, (dict, list)):
            macdv = float(macdv) if macdv else float("nan")
        else:
            macdv = float(macdv)

        if not math.isfinite(macdv):
            return

        if self.prev_macdv is None or not math.isfinite(self.prev_macdv):
            self.prev_macdv = macdv
            return

        if self.use_ema200_filter:
            ema200 = ctx.get_indicator("EMA", period=200)
            if isinstance(ema200, (dict, list)):
                ema200 = float(ema200) if ema200 else float("nan")
            else:
                ema200 = float(ema200)
            price = bar.get("bar_close") or bar.get("price") or ctx.current_price
            if not math.isfinite(ema200) or not math.isfinite(price) or price <= ema200:
                self.prev_macdv = macdv
                return

        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_macdv, macdv, self.take_profit_level):
                self.is_closing = True
                ctx.close_position(reason=f"MACD-V extreme TP ({self.prev_macdv:.1f} -> {macdv:.1f})")
                self.prev_macdv = macdv
                return
            if crossed_below(self.prev_macdv, macdv, self.momentum_exit_level):
                self.is_closing = True
                ctx.close_position(reason=f"MACD-V momentum exit ({self.prev_macdv:.1f} -> {macdv:.1f})")
                self.prev_macdv = macdv
                return

        if ctx.position_size == 0:
            if self.prev_macdv < self.oversold_level and crossed_above(self.prev_macdv, macdv, self.oversold_level):
                ctx.enter_long(reason=f"MACD-V oversold bounce ({self.prev_macdv:.1f} -> {macdv:.1f})")

        self.prev_macdv = macdv
