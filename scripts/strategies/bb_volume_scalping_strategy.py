"""Bollinger Band(20,2) 터치 + 거래량 급증 스캘핑 전략.

1분봉 스캘핑 전용. 높은 승률 평균 회귀.

규칙:
- 롱 진입: 종가 <= 하단밴드 AND 거래량 > vol_multiplier × SMA(volume, 20)
- 숏 진입: 종가 >= 상단밴드 AND 거래량 > vol_multiplier × SMA(volume, 20)
- 익절: 1.5×ATR(14)
- 손절: 1×ATR(14)
- 손익비(RR): 1.5:1 (BB의 높은 승률로 보상)
- 쿨다운: 청산 후 3봉 진입 금지
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


STRATEGY_PARAMS: dict[str, Any] = {
    "bb_period": 20,
    "bb_std_dev": 2.0,
    "atr_period": 14,
    "vol_sma_period": 20,
    "vol_multiplier": 1.5,
    "atr_tp_multiplier": 1.5,
    "atr_sl_multiplier": 1.0,
    "cooldown_bars": 3,
}

STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "bb_period": {
        "type": "integer", "min": 5, "max": 100,
        "label": "BB 기간",
        "description": "Bollinger Band 중심선(SMA) 기간",
        "group": "지표 (Indicator)",
    },
    "bb_std_dev": {
        "type": "number", "min": 0.5, "max": 4.0,
        "label": "BB 표준편차",
        "description": "밴드 폭 (기본 2.0)",
        "group": "지표 (Indicator)",
    },
    "atr_period": {
        "type": "integer", "min": 2, "max": 100,
        "label": "ATR 기간",
        "description": "ATR 계산 캔들 수",
        "group": "지표 (Indicator)",
    },
    "vol_sma_period": {
        "type": "integer", "min": 5, "max": 100,
        "label": "거래량 SMA 기간",
        "description": "평균 거래량 계산 기간",
        "group": "지표 (Indicator)",
    },
    "vol_multiplier": {
        "type": "number", "min": 1.0, "max": 5.0,
        "label": "거래량 배수 필터",
        "description": "거래량 > SMA × 이 배수일 때만 진입 (기본 1.5)",
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
        "description": "진입가 대비 ATR × 배수 손실 시 청산 (기본 1.0)",
        "group": "청산 (Exit)",
    },
    "cooldown_bars": {
        "type": "integer", "min": 0, "max": 60,
        "label": "쿨다운 봉 수",
        "description": "청산 후 N봉 동안 진입 금지",
        "group": "리스크 (Risk)",
    },
}


class BbVolumeScalpingStrategy(Strategy):
    """Bollinger Band(20,2) + 거래량 급증 스캘핑 전략.

    진입:
    - 롱: 종가 <= BB 하단밴드 AND 거래량 > 1.5 × SMA(vol,20)
    - 숏: 종가 >= BB 상단밴드 AND 거래량 > 1.5 × SMA(vol,20)

    청산:
    - 익절: 진입가 ± 1.5×ATR(14)
    - 손절: 진입가 ∓ 1×ATR(14)
    - 손익비 1.5:1, 쿨다운 3봉
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.bb_period = int(p["bb_period"])
        self.bb_std_dev = float(p["bb_std_dev"])
        self.atr_period = int(p["atr_period"])
        self.vol_sma_period = int(p["vol_sma_period"])
        self.vol_multiplier = float(p["vol_multiplier"])
        self.atr_tp_multiplier = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.cooldown_bars = int(p["cooldown_bars"])

        if self.bb_period <= 1:
            raise ValueError("bb_period must be > 1")
        if self.atr_period <= 1:
            raise ValueError("atr_period must be > 1")

        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self._bars_since_close: int | None = None

        self.params = {
            "bb_period": self.bb_period,
            "bb_std_dev": self.bb_std_dev,
            "atr_period": self.atr_period,
            "vol_sma_period": self.vol_sma_period,
            "vol_multiplier": self.vol_multiplier,
            "atr_tp_multiplier": self.atr_tp_multiplier,
            "atr_sl_multiplier": self.atr_sl_multiplier,
            "cooldown_bars": self.cooldown_bars,
        }
        self.indicator_config = {
            "BBANDS": {"period": self.bb_period, "nbdevup": self.bb_std_dev, "nbdevdn": self.bb_std_dev},
            "ATR": {"period": self.atr_period},
            "SMA": {"period": self.vol_sma_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "BBANDS")
        register_talib_indicator_all_outputs(ctx, "ATR")
        register_talib_indicator_all_outputs(ctx, "SMA")
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

        # BB 조회 (dict: upperband, middleband, lowerband)
        bb = ctx.get_indicator(
            "BBANDS",
            timeperiod=self.bb_period,
            nbdevup=self.bb_std_dev,
            nbdevdn=self.bb_std_dev,
        )
        if not isinstance(bb, dict):
            return

        upper = float(bb.get("upperband", bb.get("output_0", math.nan)))
        lower = float(bb.get("lowerband", bb.get("output_2", math.nan)))
        if not math.isfinite(upper) or not math.isfinite(lower):
            return

        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        if not math.isfinite(atr) or atr <= 0:
            return

        # 거래량 필터: 현재 거래량 vs 평균 거래량
        vol_avg = float(ctx.get_indicator("SMA", period=self.vol_sma_period, price="volume"))
        current_vol = float(bar.get("volume", 0))
        if not math.isfinite(vol_avg) or vol_avg <= 0:
            return
        volume_surge = current_vol > self.vol_multiplier * vol_avg

        if ctx.position_size != 0:
            return

        # 쿨다운 체크
        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            return

        close = ctx.current_price

        # 롱: 종가 <= BB 하단 + 거래량 급증
        if close <= lower and volume_surge:
            self.tp_price = close + self.atr_tp_multiplier * atr
            self.sl_price = close - self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_long(
                reason=f"BB Long (C={close:.2f}<=LB={lower:.2f} vol={current_vol:.0f}>{vol_avg:.0f}) "
                       f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
            )

        # 숏: 종가 >= BB 상단 + 거래량 급증
        elif close >= upper and volume_surge:
            self.tp_price = close - self.atr_tp_multiplier * atr
            self.sl_price = close + self.atr_sl_multiplier * atr
            self._bars_since_close = None
            ctx.enter_short(
                reason=f"BB Short (C={close:.2f}>=UB={upper:.2f} vol={current_vol:.0f}>{vol_avg:.0f}) "
                       f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}",
            )
