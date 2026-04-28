"""Hybrid Regime-Switching Scalping (1m).

Ernest Chan 류의 "추세장은 모멘텀, 횡보장은 평균회귀" 컨셉을 결합한 단일 전략.

레짐 판별:
- ADX(14) > adx_trend_threshold  →  TREND mode (VWAP+EMA 추세추종)
- ADX(14) < adx_range_threshold  →  RANGE mode (BB+RSI 평균회귀)
- 그 사이(회색지대)              →  관망

진입/청산 규칙은 strategy_a (vwap_ema) / strategy_b (bb_rsi)와 동일하지만,
ADX 기반으로 어느 한쪽만 활성화된다.

청산은 모드에 무관하게 항상 실행 (포지션 보호 우선).
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
    # 레짐 판별
    "adx_period": 14,
    "adx_trend_threshold": 25.0,
    "adx_range_threshold": 20.0,
    # 추세 모드 (A)
    "ema_fast": 9,
    "ema_slow": 21,
    "vwap_period": 60,
    "trend_atr_tp_multiplier": 1.5,
    "trend_atr_sl_multiplier": 1.0,
    # 회귀 모드 (B)
    "bb_period": 20,
    "bb_stddev": 2.0,
    "rsi_period": 14,
    "rsi_long_level": 25.0,
    "rsi_short_level": 75.0,
    "range_atr_sl_multiplier": 1.0,
    # 공통
    "atr_period": 14,
    "cooldown_bars": 3,
}


class HybridRegimeScalpingStrategy(Strategy):
    """ADX 기반 레짐 스위칭 (TREND ↔ RANGE) 1분봉 스캘핑."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.adx_period = int(p["adx_period"])
        self.adx_trend_threshold = float(p["adx_trend_threshold"])
        self.adx_range_threshold = float(p["adx_range_threshold"])
        self.ema_fast = int(p["ema_fast"])
        self.ema_slow = int(p["ema_slow"])
        self.vwap_period = int(p["vwap_period"])
        self.trend_atr_tp_multiplier = float(p["trend_atr_tp_multiplier"])
        self.trend_atr_sl_multiplier = float(p["trend_atr_sl_multiplier"])
        self.bb_period = int(p["bb_period"])
        self.bb_stddev = float(p["bb_stddev"])
        self.rsi_period = int(p["rsi_period"])
        self.rsi_long_level = float(p["rsi_long_level"])
        self.rsi_short_level = float(p["rsi_short_level"])
        self.range_atr_sl_multiplier = float(p["range_atr_sl_multiplier"])
        self.atr_period = int(p["atr_period"])
        self.cooldown_bars = int(p["cooldown_bars"])

        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be < ema_slow")
        if self.adx_range_threshold > self.adx_trend_threshold:
            raise ValueError("adx_range_threshold must be <= adx_trend_threshold")

        # 상태
        self.prev_close: float | None = None
        self.prev_ema_fast: float | None = None
        self.is_closing: bool = False
        self.tp_price: float = 0.0           # 추세 모드 TP
        self.sl_price: float = 0.0           # 공통 SL
        self.middle_target: float = 0.0      # 회귀 모드 TP (BB middle)
        self.entry_mode: str = ""            # "TREND" or "RANGE"
        self._bars_since_close: int | None = None

        self.params = dict(p)
        self.indicator_config = {
            "EMA_FAST": {"period": self.ema_fast},
            "EMA_SLOW": {"period": self.ema_slow},
            "VWAP": {"period": self.vwap_period},
            "BBANDS": {"period": self.bb_period, "nbdevup": self.bb_stddev, "nbdevdn": self.bb_stddev},
            "RSI": {"period": self.rsi_period},
            "ADX": {"period": self.adx_period},
            "ATR": {"period": self.atr_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "EMA")
        register_talib_indicator_all_outputs(ctx, "BBANDS")
        register_talib_indicator_all_outputs(ctx, "RSI")
        register_talib_indicator_all_outputs(ctx, "ADX")
        register_talib_indicator_all_outputs(ctx, "ATR")
        _register_rolling_vwap(ctx)
        self.prev_close = None
        self.prev_ema_fast = None
        self.is_closing = False
        self.tp_price = 0.0
        self.sl_price = 0.0
        self.middle_target = 0.0
        self.entry_mode = ""
        self._bars_since_close = None

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False

        if ctx.get_open_orders():
            return

        # ===== 청산 (모드별) =====
        if ctx.position_size != 0 and not self.is_closing:
            price = ctx.current_price
            if self.entry_mode == "TREND":
                if ctx.position_size > 0:
                    if price >= self.tp_price:
                        self._exit(ctx, f"TP Long {price:.2f}>={self.tp_price:.2f}", "TAKE_PROFIT")
                        return
                    if price <= self.sl_price:
                        self._exit(ctx, f"SL Long {price:.2f}<={self.sl_price:.2f}", "STOP_LOSS")
                        return
                else:
                    if price <= self.tp_price:
                        self._exit(ctx, f"TP Short {price:.2f}<={self.tp_price:.2f}", "TAKE_PROFIT")
                        return
                    if price >= self.sl_price:
                        self._exit(ctx, f"SL Short {price:.2f}>={self.sl_price:.2f}", "STOP_LOSS")
                        return
            elif self.entry_mode == "RANGE":
                if ctx.position_size > 0:
                    if price >= self.middle_target:
                        self._exit(ctx, f"TP Long mid {price:.2f}>={self.middle_target:.2f}", "TAKE_PROFIT")
                        return
                    if price <= self.sl_price:
                        self._exit(ctx, f"SL Long {price:.2f}<={self.sl_price:.2f}", "STOP_LOSS")
                        return
                else:
                    if price <= self.middle_target:
                        self._exit(ctx, f"TP Short mid {price:.2f}<={self.middle_target:.2f}", "TAKE_PROFIT")
                        return
                    if price >= self.sl_price:
                        self._exit(ctx, f"SL Short {price:.2f}>={self.sl_price:.2f}", "STOP_LOSS")
                        return

        if not bool(bar.get("is_new_bar", True)):
            return

        if self._bars_since_close is not None:
            self._bars_since_close += 1

        # 지표 조회
        adx = float(ctx.get_indicator("ADX", period=self.adx_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        price = ctx.current_price

        ema_fast = float(ctx.get_indicator("EMA", period=self.ema_fast))
        ema_slow = float(ctx.get_indicator("EMA", period=self.ema_slow))
        vwap = float(ctx.get_indicator("VWAP", period=self.vwap_period))

        # prev 갱신용 사전 체크
        if math.isfinite(price) and math.isfinite(ema_fast):
            prev_close_snapshot = self.prev_close
            prev_ema_snapshot = self.prev_ema_fast
        else:
            prev_close_snapshot = None
            prev_ema_snapshot = None

        if not all(math.isfinite(v) for v in (adx, atr, price, ema_fast, ema_slow, vwap)) or atr <= 0:
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

        # ===== 레짐 판별 =====
        regime: str
        if adx >= self.adx_trend_threshold:
            regime = "TREND"
        elif adx < self.adx_range_threshold:
            regime = "RANGE"
        else:
            regime = "NONE"
        if regime == "TREND":
            self._try_trend_entry(
                ctx,
                price=price,
                prev_close=prev_close_snapshot if prev_close_snapshot is not None else self.prev_close,
                prev_ema_fast=prev_ema_snapshot if prev_ema_snapshot is not None else self.prev_ema_fast,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
                vwap=vwap,
                atr=atr,
                adx=adx,
            )
        elif regime == "RANGE":
            self._try_range_entry(ctx, price=price, atr=atr, adx=adx)

        self.prev_close = price
        self.prev_ema_fast = ema_fast

    # ----- helpers -----

    def _exit(self, ctx: StrategyContext, reason: str, exit_reason: str) -> None:
        self.is_closing = True
        self._bars_since_close = 0
        self.entry_mode = ""
        ctx.close_position(reason=reason, exit_reason=exit_reason)

    def _try_trend_entry(
        self,
        ctx: StrategyContext,
        *,
        price: float,
        prev_close: float,
        prev_ema_fast: float,
        ema_fast: float,
        ema_slow: float,
        vwap: float,
        atr: float,
        adx: float,
    ) -> None:
        long_regime = ema_fast > ema_slow and price > vwap
        short_regime = ema_fast < ema_slow and price < vwap
        long_break = prev_close < prev_ema_fast and price > ema_fast
        short_break = prev_close > prev_ema_fast and price < ema_fast

        if long_regime and long_break:
            self.tp_price = price + self.trend_atr_tp_multiplier * atr
            self.sl_price = price - self.trend_atr_sl_multiplier * atr
            self.entry_mode = "TREND"
            self._bars_since_close = None
            ctx.enter_long(
                reason=(
                    f"[TREND] Long ADX={adx:.1f} EMA{self.ema_fast}={ema_fast:.2f}>"
                    f"EMA{self.ema_slow}={ema_slow:.2f} P={price:.2f}>VWAP={vwap:.2f} "
                    f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                ),
            )
        elif short_regime and short_break:
            self.tp_price = price - self.trend_atr_tp_multiplier * atr
            self.sl_price = price + self.trend_atr_sl_multiplier * atr
            self.entry_mode = "TREND"
            self._bars_since_close = None
            ctx.enter_short(
                reason=(
                    f"[TREND] Short ADX={adx:.1f} EMA{self.ema_fast}={ema_fast:.2f}<"
                    f"EMA{self.ema_slow}={ema_slow:.2f} P={price:.2f}<VWAP={vwap:.2f} "
                    f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                ),
            )

    def _try_range_entry(
        self,
        ctx: StrategyContext,
        *,
        price: float,
        atr: float,
        adx: float,
    ) -> None:
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

        if not all(math.isfinite(v) for v in (upper, middle, lower, rsi)):
            return

        if price <= lower and rsi < self.rsi_long_level:
            self.middle_target = middle
            self.sl_price = price - self.range_atr_sl_multiplier * atr
            self.entry_mode = "RANGE"
            self._bars_since_close = None
            ctx.enter_long(
                reason=(
                    f"[RANGE] Long ADX={adx:.1f} P={price:.2f}<L={lower:.2f} "
                    f"RSI={rsi:.1f}<{self.rsi_long_level:.0f} TP_mid={middle:.2f} SL={self.sl_price:.2f}"
                ),
            )
            return

        if price >= upper and rsi > self.rsi_short_level:
            self.middle_target = middle
            self.sl_price = price + self.range_atr_sl_multiplier * atr
            self.entry_mode = "RANGE"
            self._bars_since_close = None
            ctx.enter_short(
                reason=(
                    f"[RANGE] Short ADX={adx:.1f} P={price:.2f}>U={upper:.2f} "
                    f"RSI={rsi:.1f}>{self.rsi_short_level:.0f} TP_mid={middle:.2f} SL={self.sl_price:.2f}"
                ),
            )
