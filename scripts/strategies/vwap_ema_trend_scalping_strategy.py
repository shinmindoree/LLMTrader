"""VWAP + EMA Trend-Following Scalping (1m).

해외 단타 트레이더(Linda Raschke / Al Brooks 류)의 "추세 + 되돌림" 컨셉을
1분봉 BTCUSDT 선물에 맞게 단순화한 전략.

규칙:
- 추세 필터: EMA(fast) > EMA(slow) AND 가격 > VWAP  → 롱 모드
              EMA(fast) < EMA(slow) AND 가격 < VWAP  → 숏 모드
- 진입 트리거(되돌림 후 재돌파):
    * 롱: 가격이 직전 봉에서 EMA(fast) 아래로 내려갔다가 현재 봉에서 다시 위로 돌파
    * 숏: 반대
- 청산: ATR(14) × TP/SL 배수 (기본 TP 1.5×ATR, SL 1.0×ATR)
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


def _register_rolling_vwap(ctx: StrategyContext) -> None:
    """Rolling VWAP(N) = sum(typical * volume, last N) / sum(volume, last N)."""
    try:
        import numpy as np  # type: ignore
    except Exception:  # noqa: BLE001
        return

    def _vwap(inner_ctx: Any, *args: Any, **kwargs: Any) -> float:
        period = int(kwargs.get("period", kwargs.get("timeperiod", 20)))
        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw = inputs()
        high = np.asarray(list(raw.get("high", [])), dtype="float64")
        low = np.asarray(list(raw.get("low", [])), dtype="float64")
        close = np.asarray(list(raw.get("close", [])), dtype="float64")
        volume = np.asarray(list(raw.get("volume", [])), dtype="float64")
        n = len(close)
        if n < period:
            return float("nan")
        typical = (high[-period:] + low[-period:] + close[-period:]) / 3.0
        vol_slice = volume[-period:]
        vol_sum = float(np.sum(vol_slice))
        if vol_sum <= 0:
            return float("nan")
        return float(np.sum(typical * vol_slice) / vol_sum)

    ctx.register_indicator("VWAP", _vwap)


STRATEGY_PARAMS: dict[str, Any] = {
    "ema_fast": 9,
    "ema_slow": 21,
    "vwap_period": 60,          # 1분봉 기준 60분 = 1시간 VWAP
    "atr_period": 14,
    "atr_tp_multiplier": 1.5,
    "atr_sl_multiplier": 1.0,
    "cooldown_bars": 3,
}


class VwapEmaTrendScalpingStrategy(Strategy):
    """VWAP + EMA 정렬 + 되돌림 진입 추세추종 스캘핑 (1분봉)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.ema_fast = int(p["ema_fast"])
        self.ema_slow = int(p["ema_slow"])
        self.vwap_period = int(p["vwap_period"])
        self.atr_period = int(p["atr_period"])
        self.atr_tp_multiplier = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.cooldown_bars = int(p["cooldown_bars"])

        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be < ema_slow")

        self.prev_close: float | None = None
        self.prev_ema_fast: float | None = None
        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self._bars_since_close: int | None = None

        self.params = dict(p)
        self.indicator_config = {
            "EMA_FAST": {"period": self.ema_fast},
            "EMA_SLOW": {"period": self.ema_slow},
            "ATR": {"period": self.atr_period},
            "VWAP": {"period": self.vwap_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "EMA")
        register_talib_indicator_all_outputs(ctx, "ATR")
        _register_rolling_vwap(ctx)
        self.prev_close = None
        self.prev_ema_fast = None
        self.is_closing = False
        self.tp_price = 0.0
        self.sl_price = 0.0
        self._bars_since_close = None

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        if ctx.get_open_orders():
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
            else:
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

        if not bool(bar.get("is_new_bar", True)):
            return

        if self._bars_since_close is not None:
            self._bars_since_close += 1

        ema_fast = float(ctx.get_indicator("EMA", period=self.ema_fast))
        ema_slow = float(ctx.get_indicator("EMA", period=self.ema_slow))
        vwap = float(ctx.get_indicator("VWAP", period=self.vwap_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        price = ctx.current_price

        if not all(math.isfinite(v) for v in (ema_fast, ema_slow, vwap, atr, price)) or atr <= 0:
            self.prev_close = price if math.isfinite(price) else self.prev_close
            self.prev_ema_fast = ema_fast if math.isfinite(ema_fast) else self.prev_ema_fast
            return

        if self.prev_close is None or self.prev_ema_fast is None:
            self.prev_close = price
            self.prev_ema_fast = ema_fast
            return

        if ctx.position_size != 0:
            self.prev_close = price
            self.prev_ema_fast = ema_fast
            return

        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            self.prev_close = price
            self.prev_ema_fast = ema_fast
            return

        long_regime = ema_fast > ema_slow and price > vwap
        short_regime = ema_fast < ema_slow and price < vwap

        # 되돌림 후 재돌파:
        # 롱: 직전봉 종가가 직전 EMA_fast 아래 → 현재 종가가 EMA_fast 위
        long_pullback_break = self.prev_close < self.prev_ema_fast and price > ema_fast
        short_pullback_break = self.prev_close > self.prev_ema_fast and price < ema_fast

        if long_regime and long_pullback_break:
            self.tp_price = price + self.atr_tp_multiplier * atr
            self.sl_price = price - self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_long(
                reason=(
                    f"VWAP+EMA Long (EMA{self.ema_fast}={ema_fast:.2f}>EMA{self.ema_slow}={ema_slow:.2f}, "
                    f"P={price:.2f}>VWAP={vwap:.2f}) TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                ),
            )
        elif short_regime and short_pullback_break:
            self.tp_price = price - self.atr_tp_multiplier * atr
            self.sl_price = price + self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_short(
                reason=(
                    f"VWAP+EMA Short (EMA{self.ema_fast}={ema_fast:.2f}<EMA{self.ema_slow}={ema_slow:.2f}, "
                    f"P={price:.2f}<VWAP={vwap:.2f}) TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                ),
            )

        self.prev_close = price
        self.prev_ema_fast = ema_fast
