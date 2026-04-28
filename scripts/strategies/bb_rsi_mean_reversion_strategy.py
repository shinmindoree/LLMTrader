"""Bollinger Bands + RSI Mean-Reversion Scalping (1m).

Kathy Lien / Boris Schlossberg 류의 평균회귀 컨셉을 BTCUSDT 1분봉에 이식.

규칙:
- 롱 진입: 종가가 BB 하단 이탈(close < lower) AND RSI(14) < 25
- 숏 진입: 종가가 BB 상단 이탈(close > upper) AND RSI(14) > 75
- 추세장 회피 필터: ADX(14) < adx_max  (기본 25)  → 횡보장에서만 작동
- 청산:
    * TP: BB 미들밴드(MA) 도달 시 청산  (평균회귀의 본질)
    * SL: ATR(14) × atr_sl_multiplier
- 쿨다운: 청산 후 N봉 진입 금지
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
                prepared_inputs["real"] = (
                    np.asarray(derived_series, dtype="float64")
                    if not hasattr(derived_series, "dtype")
                    else derived_series
                )

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
    # 일평균 ~10회 거래(30일 기준 ~300회) 타깃.
    "bb_period": 20,
    "bb_stddev": 1.75,
    "rsi_period": 14,
    "rsi_long_level": 31.0,
    "rsi_short_level": 69.0,
    "adx_period": 14,
    "adx_max": 29.0,
    "atr_period": 14,
    "atr_sl_multiplier": 1.0,
    "cooldown_bars": 4,
}


class BbRsiMeanReversionStrategy(Strategy):
    """BB + RSI 평균회귀 스캘핑 (1분봉, 횡보장 전용)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.bb_period = int(p["bb_period"])
        self.bb_stddev = float(p["bb_stddev"])
        self.rsi_period = int(p["rsi_period"])
        self.rsi_long_level = float(p["rsi_long_level"])
        self.rsi_short_level = float(p["rsi_short_level"])
        self.adx_period = int(p["adx_period"])
        self.adx_max = float(p["adx_max"])
        self.atr_period = int(p["atr_period"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.cooldown_bars = int(p["cooldown_bars"])

        self.is_closing: bool = False
        self.middle_target: float = 0.0  # BB 미들밴드 (TP target, 진입 시 스냅샷)
        self.sl_price: float = 0.0
        self._bars_since_close: int | None = None

        self.params = dict(p)
        self.indicator_config = {
            "BBANDS": {"period": self.bb_period, "nbdevup": self.bb_stddev, "nbdevdn": self.bb_stddev},
            "RSI": {"period": self.rsi_period},
            "ADX": {"period": self.adx_period},
            "ATR": {"period": self.atr_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "BBANDS")
        register_talib_indicator_all_outputs(ctx, "RSI")
        register_talib_indicator_all_outputs(ctx, "ADX")
        register_talib_indicator_all_outputs(ctx, "ATR")
        self.is_closing = False
        self.middle_target = 0.0
        self.sl_price = 0.0
        self._bars_since_close = None

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        if ctx.get_open_orders():
            return

        # ===== 청산 (BB 미들밴드 회귀 / ATR SL) =====
        if ctx.position_size != 0 and not self.is_closing:
            price = ctx.current_price
            if ctx.position_size > 0:
                if price >= self.middle_target:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(
                        reason=f"TP Long mid {price:.2f}>={self.middle_target:.2f}",
                        exit_reason="TAKE_PROFIT",
                    )
                    return
                if price <= self.sl_price:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(
                        reason=f"SL Long {price:.2f}<={self.sl_price:.2f}",
                        exit_reason="STOP_LOSS",
                    )
                    return
            else:
                if price <= self.middle_target:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(
                        reason=f"TP Short mid {price:.2f}<={self.middle_target:.2f}",
                        exit_reason="TAKE_PROFIT",
                    )
                    return
                if price >= self.sl_price:
                    self.is_closing = True
                    self._bars_since_close = 0
                    ctx.close_position(
                        reason=f"SL Short {price:.2f}>={self.sl_price:.2f}",
                        exit_reason="STOP_LOSS",
                    )
                    return

        if not bool(bar.get("is_new_bar", True)):
            return

        if self._bars_since_close is not None:
            self._bars_since_close += 1

        if ctx.position_size != 0:
            return
        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            return

        bb = ctx.get_indicator(
            "BBANDS",
            period=self.bb_period,
            nbdevup=self.bb_stddev,
            nbdevdn=self.bb_stddev,
        )
        if not isinstance(bb, dict):
            return
        upper = float(bb.get("upperband", bb.get("output_0", math.nan)))
        middle = float(bb.get("middleband", bb.get("output_1", math.nan)))
        lower = float(bb.get("lowerband", bb.get("output_2", math.nan)))

        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        adx = float(ctx.get_indicator("ADX", period=self.adx_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        price = ctx.current_price

        if not all(math.isfinite(v) for v in (upper, middle, lower, rsi, adx, atr, price)) or atr <= 0:
            return

        # 추세장 회피
        if adx >= self.adx_max:
            return

        # 롱: 가격이 BB 하단 터치/이탈 + RSI 과매도
        if price <= lower and rsi < self.rsi_long_level:
            self.middle_target = middle
            self.sl_price = price - self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_long(
                reason=(
                    f"BB+RSI Long (P={price:.2f}<L={lower:.2f}, RSI={rsi:.1f}<{self.rsi_long_level:.0f}, "
                    f"ADX={adx:.1f}) TP_mid={middle:.2f} SL={self.sl_price:.2f}"
                ),
            )
            return

        # 숏: 가격이 BB 상단 터치/이탈 + RSI 과매수
        if price >= upper and rsi > self.rsi_short_level:
            self.middle_target = middle
            self.sl_price = price + self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_short(
                reason=(
                    f"BB+RSI Short (P={price:.2f}>U={upper:.2f}, RSI={rsi:.1f}>{self.rsi_short_level:.0f}, "
                    f"ADX={adx:.1f}) TP_mid={middle:.2f} SL={self.sl_price:.2f}"
                ),
            )
