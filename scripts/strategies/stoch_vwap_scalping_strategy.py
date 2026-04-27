"""Stochastic(5,3,3) + Rolling VWAP 방향 필터 스캘핑 전략.

1분봉 스캘핑 전용. 과매도 반등 + 기관 참조가 필터.

규칙:
- 롱 진입: %K가 %D 상향 돌파 AND 둘 다 < 20 (과매도) AND 가격 > VWAP
- 숏 진입: %K가 %D 하향 돌파 AND 둘 다 > 80 (과매수) AND 가격 < VWAP
- 익절: 1.5×ATR(14)
- 손절: 0.75×ATR(14)
- 손익비(RR): 2:1
- 쿨다운: 청산 후 5봉 진입 금지
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


def _register_rolling_vwap(ctx: StrategyContext) -> None:
    """Rolling VWAP 커스텀 인디케이터 등록.

    VWAP(N) = sum(typical_price * volume, last N bars) / sum(volume, last N bars)
    typical_price = (high + low + close) / 3
    """
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
        vol_sum = np.sum(vol_slice)
        if vol_sum <= 0:
            return float("nan")
        return float(np.sum(typical * vol_slice) / vol_sum)

    ctx.register_indicator("VWAP", _vwap)


STRATEGY_PARAMS: dict[str, Any] = {
    "stoch_k_period": 5,
    "stoch_d_period": 3,
    "stoch_slowing": 3,
    "vwap_period": 20,
    "atr_period": 14,
    "oversold_level": 20.0,
    "overbought_level": 80.0,
    "atr_tp_multiplier": 1.5,
    "atr_sl_multiplier": 0.75,
    "cooldown_bars": 5,
}

STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "stoch_k_period": {
        "type": "integer", "min": 2, "max": 30,
        "label": "Stochastic %K 기간",
        "description": "%K 계산에 사용할 캔들 수",
        "group": "지표 (Indicator)",
    },
    "stoch_d_period": {
        "type": "integer", "min": 1, "max": 20,
        "label": "Stochastic %D 기간",
        "description": "%D(시그널) 이동평균 기간",
        "group": "지표 (Indicator)",
    },
    "stoch_slowing": {
        "type": "integer", "min": 1, "max": 10,
        "label": "Stochastic Slowing",
        "description": "%K 슬로잉 기간",
        "group": "지표 (Indicator)",
    },
    "vwap_period": {
        "type": "integer", "min": 5, "max": 200,
        "label": "Rolling VWAP 기간",
        "description": "VWAP 계산에 사용할 봉 수",
        "group": "지표 (Indicator)",
    },
    "atr_period": {
        "type": "integer", "min": 2, "max": 100,
        "label": "ATR 기간",
        "description": "ATR 계산 캔들 수",
        "group": "지표 (Indicator)",
    },
    "oversold_level": {
        "type": "number", "min": 5, "max": 40,
        "label": "과매도 기준",
        "description": "%K, %D 모두 이 값 미만일 때 롱 진입 허용",
        "group": "진입 (Entry)",
    },
    "overbought_level": {
        "type": "number", "min": 60, "max": 95,
        "label": "과매수 기준",
        "description": "%K, %D 모두 이 값 초과일 때 숏 진입 허용",
        "group": "진입 (Entry)",
    },
    "atr_tp_multiplier": {
        "type": "number", "min": 0.3, "max": 5.0,
        "label": "익절 ATR 배수",
        "description": "진입가 대비 ATR × 배수 이익 시 청산 (기본 1.5)",
        "group": "청산 (Exit)",
    },
    "atr_sl_multiplier": {
        "type": "number", "min": 0.1, "max": 3.0,
        "label": "손절 ATR 배수",
        "description": "진입가 대비 ATR × 배수 손실 시 청산 (기본 0.75)",
        "group": "청산 (Exit)",
    },
    "cooldown_bars": {
        "type": "integer", "min": 0, "max": 60,
        "label": "쿨다운 봉 수",
        "description": "청산 후 N봉 동안 진입 금지",
        "group": "리스크 (Risk)",
    },
}


class StochVwapScalpingStrategy(Strategy):
    """Stochastic(5,3,3) + Rolling VWAP 스캘핑 전략.

    진입:
    - 롱: %K가 %D 상향 돌파 AND 둘 다 < 20 AND 가격 > VWAP
    - 숏: %K가 %D 하향 돌파 AND 둘 다 > 80 AND 가격 < VWAP

    청산:
    - 익절: 진입가 ± 1.5×ATR(14)
    - 손절: 진입가 ∓ 0.75×ATR(14)
    - 손익비 2:1, 쿨다운 5봉
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.stoch_k_period = int(p["stoch_k_period"])
        self.stoch_d_period = int(p["stoch_d_period"])
        self.stoch_slowing = int(p["stoch_slowing"])
        self.vwap_period = int(p["vwap_period"])
        self.atr_period = int(p["atr_period"])
        self.oversold_level = float(p["oversold_level"])
        self.overbought_level = float(p["overbought_level"])
        self.atr_tp_multiplier = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.cooldown_bars = int(p["cooldown_bars"])

        if self.stoch_k_period <= 1:
            raise ValueError("stoch_k_period must be > 1")
        if self.atr_period <= 1:
            raise ValueError("atr_period must be > 1")

        self.prev_k: float | None = None
        self.prev_d: float | None = None
        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self._bars_since_close: int | None = None

        self.params = {
            "stoch_k_period": self.stoch_k_period,
            "stoch_d_period": self.stoch_d_period,
            "stoch_slowing": self.stoch_slowing,
            "vwap_period": self.vwap_period,
            "atr_period": self.atr_period,
            "oversold_level": self.oversold_level,
            "overbought_level": self.overbought_level,
            "atr_tp_multiplier": self.atr_tp_multiplier,
            "atr_sl_multiplier": self.atr_sl_multiplier,
            "cooldown_bars": self.cooldown_bars,
        }
        self.indicator_config = {
            "STOCH": {
                "fastk_period": self.stoch_k_period,
                "slowk_period": self.stoch_slowing,
                "slowd_period": self.stoch_d_period,
            },
            "ATR": {"period": self.atr_period},
            "VWAP": {"period": self.vwap_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "STOCH")
        register_talib_indicator_all_outputs(ctx, "ATR")
        _register_rolling_vwap(ctx)
        self.prev_k = None
        self.prev_d = None
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

        # Stochastic 조회 (dict: slowk, slowd)
        stoch = ctx.get_indicator(
            "STOCH",
            fastk_period=self.stoch_k_period,
            slowk_period=self.stoch_slowing,
            slowd_period=self.stoch_d_period,
        )
        if not isinstance(stoch, dict):
            return

        k = float(stoch.get("slowk", stoch.get("output_0", math.nan)))
        d = float(stoch.get("slowd", stoch.get("output_1", math.nan)))
        if not math.isfinite(k) or not math.isfinite(d):
            return

        if self.prev_k is None or self.prev_d is None:
            self.prev_k = k
            self.prev_d = d
            return

        if ctx.position_size != 0:
            self.prev_k = k
            self.prev_d = d
            return

        # 쿨다운 체크
        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            self.prev_k = k
            self.prev_d = d
            return

        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        vwap = float(ctx.get_indicator("VWAP", period=self.vwap_period))
        if not math.isfinite(atr) or atr <= 0 or not math.isfinite(vwap):
            self.prev_k = k
            self.prev_d = d
            return

        price = ctx.current_price
        k_crossed_above_d = self.prev_k <= self.prev_d and k > d
        k_crossed_below_d = self.prev_k >= self.prev_d and k < d

        # 롱: %K > %D 크로스 + 과매도 + 가격 > VWAP
        if k_crossed_above_d and k < self.oversold_level and d < self.oversold_level and price > vwap:
            self.tp_price = price + self.atr_tp_multiplier * atr
            self.sl_price = price - self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_long(
                reason=f"Stoch+VWAP Long (K={k:.1f}>D={d:.1f} P={price:.2f}>VWAP={vwap:.2f}) "
                       f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
            )

        # 숏: %K < %D 크로스 + 과매수 + 가격 < VWAP
        elif k_crossed_below_d and k > self.overbought_level and d > self.overbought_level and price < vwap:
            self.tp_price = price - self.atr_tp_multiplier * atr
            self.sl_price = price + self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_short(
                reason=f"Stoch+VWAP Short (K={k:.1f}<D={d:.1f} P={price:.2f}<VWAP={vwap:.2f}) "
                       f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
            )

        self.prev_k = k
        self.prev_d = d
