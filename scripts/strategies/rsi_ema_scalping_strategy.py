"""RSI(7) + EMA(20) 추세 필터 + ATR TP/SL 스캘핑 전략.

1분봉 스캘핑 전용. 평균 회귀 + 추세 필터 결합.

규칙:
- 롱 진입: RSI(7)가 30 상향 돌파 AND 가격 > EMA(20) (상승 추세)
- 숏 진입: RSI(7)가 70 하향 돌파 AND 가격 < EMA(20) (하락 추세)
- 익절: 1×ATR(14)
- 손절: 0.5×ATR(14)
- 손익비(RR): 2:1
- 쿨다운: 청산 후 5봉 동안 진입 금지 (과매매 방지)
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
            else:
                derived_fn = abstract.Function(price_source.strip().upper())
                derived_result = derived_fn(prepared_inputs)
                if isinstance(derived_result, dict):
                    derived_series = list(derived_result.values())[0]
                elif isinstance(derived_result, (list, tuple)):
                    derived_series = derived_result[0]
                else:
                    derived_series = derived_result
                prepared_inputs["real"] = np.asarray(derived_series, dtype="float64") if not hasattr(derived_series, "dtype") else derived_series

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


def crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    return current <= level < prev


STRATEGY_PARAMS: dict[str, Any] = {
    "rsi_period": 7,
    "ema_period": 20,
    "atr_period": 14,
    "long_entry_rsi": 30.0,
    "short_entry_rsi": 70.0,
    "atr_tp_multiplier": 1.0,
    "atr_sl_multiplier": 0.5,
    "cooldown_bars": 5,
}

STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "rsi_period": {
        "type": "integer", "min": 2, "max": 50,
        "label": "RSI 기간",
        "description": "RSI 계산 캔들 수 (짧을수록 민감)",
        "group": "지표 (Indicator)",
    },
    "ema_period": {
        "type": "integer", "min": 5, "max": 200,
        "label": "EMA 기간",
        "description": "추세 필터용 EMA 기간",
        "group": "지표 (Indicator)",
    },
    "atr_period": {
        "type": "integer", "min": 2, "max": 100,
        "label": "ATR 기간",
        "description": "ATR 계산 캔들 수",
        "group": "지표 (Indicator)",
    },
    "long_entry_rsi": {
        "type": "number", "min": 10, "max": 50,
        "label": "롱 진입 RSI",
        "description": "RSI가 이 값을 상향 돌파 + 가격>EMA 시 롱 진입",
        "group": "진입 (Entry)",
    },
    "short_entry_rsi": {
        "type": "number", "min": 50, "max": 90,
        "label": "숏 진입 RSI",
        "description": "RSI가 이 값을 하향 돌파 + 가격<EMA 시 숏 진입",
        "group": "진입 (Entry)",
    },
    "atr_tp_multiplier": {
        "type": "number", "min": 0.3, "max": 5.0,
        "label": "익절 ATR 배수",
        "description": "진입가 대비 ATR × 배수 이익 시 청산 (기본 1.0)",
        "group": "청산 (Exit)",
    },
    "atr_sl_multiplier": {
        "type": "number", "min": 0.1, "max": 3.0,
        "label": "손절 ATR 배수",
        "description": "진입가 대비 ATR × 배수 손실 시 청산 (기본 0.5)",
        "group": "청산 (Exit)",
    },
    "cooldown_bars": {
        "type": "integer", "min": 0, "max": 60,
        "label": "쿨다운 봉 수",
        "description": "청산 후 N봉 동안 진입 금지 (과매매 방지)",
        "group": "리스크 (Risk)",
    },
}


class RsiEmaScalpingStrategy(Strategy):
    """RSI(7) + EMA(20) 추세 필터 스캘핑 전략.

    진입:
    - 롱: RSI(7) 30 상향 돌파 AND 가격 > EMA(20)
    - 숏: RSI(7) 70 하향 돌파 AND 가격 < EMA(20)

    청산:
    - 익절: 진입가 ± 1×ATR(14)
    - 손절: 진입가 ∓ 0.5×ATR(14)
    - 손익비 2:1, 쿨다운 5봉
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.rsi_period = int(p["rsi_period"])
        self.ema_period = int(p["ema_period"])
        self.atr_period = int(p["atr_period"])
        self.long_entry_rsi = float(p["long_entry_rsi"])
        self.short_entry_rsi = float(p["short_entry_rsi"])
        self.atr_tp_multiplier = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.cooldown_bars = int(p["cooldown_bars"])

        if self.rsi_period <= 1:
            raise ValueError("rsi_period must be > 1")
        if self.ema_period <= 1:
            raise ValueError("ema_period must be > 1")
        if self.atr_period <= 1:
            raise ValueError("atr_period must be > 1")

        self.prev_rsi: float | None = None
        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self._bars_since_close: int | None = None

        self.params = {
            "rsi_period": self.rsi_period,
            "ema_period": self.ema_period,
            "atr_period": self.atr_period,
            "long_entry_rsi": self.long_entry_rsi,
            "short_entry_rsi": self.short_entry_rsi,
            "atr_tp_multiplier": self.atr_tp_multiplier,
            "atr_sl_multiplier": self.atr_sl_multiplier,
            "cooldown_bars": self.cooldown_bars,
        }
        self.indicator_config = {
            "RSI": {"period": self.rsi_period},
            "EMA": {"period": self.ema_period},
            "ATR": {"period": self.atr_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "RSI")
        register_talib_indicator_all_outputs(ctx, "EMA")
        register_talib_indicator_all_outputs(ctx, "ATR")
        self.prev_rsi = None
        self.is_closing = False
        self.tp_price = 0.0
        self.sl_price = 0.0
        self._bars_since_close = None

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        # ===== ATR TP/SL 청산 =====
        if ctx.position_size != 0 and not self.is_closing:
            price = ctx.current_price
            if ctx.position_size > 0:
                if price >= self.tp_price:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(reason=f"TP Long {price:.2f}>={self.tp_price:.2f}", exit_reason="TAKE_PROFIT")
                    return
                if price <= self.sl_price:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(reason=f"SL Long {price:.2f}<={self.sl_price:.2f}", exit_reason="STOP_LOSS")
                    return
            elif ctx.position_size < 0:
                if price <= self.tp_price:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(reason=f"TP Short {price:.2f}<={self.tp_price:.2f}", exit_reason="TAKE_PROFIT")
                    return
                if price >= self.sl_price:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(reason=f"SL Short {price:.2f}>={self.sl_price:.2f}", exit_reason="STOP_LOSS")
                    return

        # ===== 진입은 새 봉에서만 =====
        if not bool(bar.get("is_new_bar", True)):
            return

        if self._bars_since_close is not None:
            self._bars_since_close += 1

        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        if not math.isfinite(rsi):
            return

        if self.prev_rsi is None or not math.isfinite(self.prev_rsi):
            self.prev_rsi = rsi
            return

        if ctx.position_size == 0:
            if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
                self.prev_rsi = rsi
                return

            ema = float(ctx.get_indicator("EMA", period=self.ema_period))
            atr = float(ctx.get_indicator("ATR", period=self.atr_period))
            if not math.isfinite(ema) or not math.isfinite(atr) or atr <= 0:
                self.prev_rsi = rsi
                return

            price = ctx.current_price

            if crossed_above(self.prev_rsi, rsi, self.long_entry_rsi) and price > ema:
                self.tp_price = price + self.atr_tp_multiplier * atr
                self.sl_price = price - self.atr_sl_multiplier * atr
                self._bars_since_close = None
                ctx.enter_long(
                    reason=f"RSI+EMA Long ({self.prev_rsi:.1f}->{rsi:.1f} P>{ema:.1f}) "
                           f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
                )

            elif crossed_below(self.prev_rsi, rsi, self.short_entry_rsi) and price < ema:
                self.tp_price = price - self.atr_tp_multiplier * atr
                self.sl_price = price + self.atr_sl_multiplier * atr
                self._bars_since_close = None
                ctx.enter_short(
                    reason=f"RSI+EMA Short ({self.prev_rsi:.1f}->{rsi:.1f} P<{ema:.1f}) "
                           f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
                )

        self.prev_rsi = rsi
