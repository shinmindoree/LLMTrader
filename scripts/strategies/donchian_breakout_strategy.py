"""Donchian Channel Breakout (Turtle-style) for BTCUSDT.

Richard Dennis / William Eckhardt의 터틀 트레이딩 컨셉.
짧은 룩백으로 1분~5분봉 스캘핑에 맞게 변형.

규칙:
- 롱 진입: 종가가 직전 N봉의 최고가를 돌파(break high) AND ADX>=trend_min (추세장)
- 숏 진입: 종가가 직전 N봉의 최저가를 돌파 AND ADX>=trend_min
- 청산:
    * TP: ATR × atr_tp_multiplier
    * SL: ATR × atr_sl_multiplier (ATR 기반 chandelier 트레일링은 옵션)
    * 시간 만료: max_hold_bars
- 거래량 필터(옵션): 현재 봉 거래량이 SMA × volume_mult 이상
- 쿨다운: cooldown_bars
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
    "donchian_period": 20,         # 직전 N봉 최고/최저
    "adx_period": 14,
    "adx_min": 20.0,               # 추세장 진입 (이상이어야 진입)
    "atr_period": 14,
    "atr_tp_multiplier": 2.0,
    "atr_sl_multiplier": 1.0,
    "ema_period": 50,              # 추세 필터: 롱은 close>EMA, 숏은 close<EMA
    "use_ema_filter": True,
    "volume_ma_period": 20,
    "volume_mult": 0.0,            # 0이면 비활성화
    "max_hold_bars": 30,
    "cooldown_bars": 2,
    "allow_long": True,
    "allow_short": True,
    "fade_mode": False,            # True면 돌파 페이드(역방향) 진입
    "rsi_period": 14,
    "rsi_long_max": 100.0,         # fade 롱(상단 돌파→숏 페이드 X, 하단 돌파→롱 페이드)에서 RSI 최대값
    "rsi_short_min": 0.0,
}


class DonchianBreakoutStrategy(Strategy):
    """Donchian breakout (turtle-lite) 모멘텀 전략."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        self.donchian_period = int(p["donchian_period"])
        self.adx_period = int(p["adx_period"])
        self.adx_min = float(p["adx_min"])
        self.atr_period = int(p["atr_period"])
        self.atr_tp_multiplier = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.ema_period = int(p["ema_period"])
        self.use_ema_filter = bool(p["use_ema_filter"])
        self.volume_ma_period = int(p["volume_ma_period"])
        self.volume_mult = float(p["volume_mult"])
        self.max_hold_bars = int(p["max_hold_bars"])
        self.cooldown_bars = int(p["cooldown_bars"])
        self.allow_long = bool(p["allow_long"])
        self.allow_short = bool(p["allow_short"])
        self.fade_mode = bool(p["fade_mode"])
        self.rsi_period = int(p["rsi_period"])
        self.rsi_long_max = float(p["rsi_long_max"])
        self.rsi_short_min = float(p["rsi_short_min"])

        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self._bars_since_close: int | None = None
        self._bars_in_position: int = 0

        # 수동으로 직전 N봉 high/low 트래킹 (talib MAX/MIN로 처리)
        self.params = dict(p)
        self.indicator_config = {
            "ADX": {"period": self.adx_period},
            "ATR": {"period": self.atr_period},
            "EMA": {"period": self.ema_period},
            "MAX": {"period": self.donchian_period},
            "MIN": {"period": self.donchian_period},
            "SMA": {"period": self.volume_ma_period},
            "RSI": {"period": self.rsi_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        for n in ("ADX", "ATR", "EMA", "MAX", "MIN", "SMA", "RSI"):
            register_talib_indicator_all_outputs(ctx, n)
        self.is_closing = False
        self.tp_price = 0.0
        self.sl_price = 0.0
        self._bars_since_close = None
        self._bars_in_position = 0

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False
            self._bars_in_position = 0

        if ctx.get_open_orders():
            return

        # ===== 청산 (ATR TP / ATR SL / 시간 만료) =====
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

        # 보유 중: 시간 청산만
        if ctx.position_size != 0:
            self._bars_in_position += 1
            if self.max_hold_bars > 0 and self._bars_in_position >= self.max_hold_bars and not self.is_closing:
                self.is_closing = True
                self._bars_since_close = 0
                ctx.close_position(reason=f"Time exit after {self._bars_in_position} bars", exit_reason="TIME_EXIT")
            return

        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            return

        # 직전 N봉(현재 포함된 MAX는 현재 high를 포함하므로, 직전 N-1봉의 max high가 필요).
        # 여기서는 단순화: MAX(period=N)의 현재 값 = 최근 N봉(현재 포함) 최고가. 진입 조건은 close > max_prev → close==MAX와 같음.
        # 정통 turtle은 직전 N봉을 보지만, 1m 스캘핑에서는 close >= MAX(N)을 신호로 사용해도 충분.
        max_high = float(ctx.get_indicator("MAX", period=self.donchian_period, price="high"))
        min_low = float(ctx.get_indicator("MIN", period=self.donchian_period, price="low"))
        adx = float(ctx.get_indicator("ADX", period=self.adx_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        ema = float(ctx.get_indicator("EMA", period=self.ema_period))
        vol_ma = float(ctx.get_indicator("SMA", period=self.volume_ma_period, price="volume"))
        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        price = ctx.current_price
        cur_volume = float(bar.get("volume", 0.0))
        bar_high = float(bar.get("high", price))
        bar_low = float(bar.get("low", price))

        if not all(math.isfinite(v) for v in (max_high, min_low, adx, atr, ema, rsi, price)) or atr <= 0:
            return

        if adx < self.adx_min:
            return

        if self.volume_mult > 0 and vol_ma > 0 and cur_volume < vol_ma * self.volume_mult:
            return

        if self.fade_mode:
            # FADE: 상단 돌파(고점 갱신)는 숏, 하단 돌파(저점 갱신)는 롱
            # 롱 페이드 = 하단 돌파 시점에 mean revert를 기대하고 롱
            if self.allow_long and bar_low <= min_low and rsi <= self.rsi_long_max:
                if self.use_ema_filter and price > ema:
                    # uptrend에서만 dip 매수
                    self.tp_price = price + self.atr_tp_multiplier * atr
                    self.sl_price = price - self.atr_sl_multiplier * atr
                    self._bars_since_close = None
                    self._bars_in_position = 0
                    ctx.enter_long(
                        reason=(
                            f"Donchian Fade Long (L={bar_low:.2f}<=MIN={min_low:.2f}, "
                            f"ADX={adx:.1f} RSI={rsi:.1f} price>EMA={ema:.2f}) "
                            f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                        ),
                    )
                    return
                elif not self.use_ema_filter:
                    self.tp_price = price + self.atr_tp_multiplier * atr
                    self.sl_price = price - self.atr_sl_multiplier * atr
                    self._bars_since_close = None
                    self._bars_in_position = 0
                    ctx.enter_long(
                        reason=(
                            f"Donchian Fade Long (L={bar_low:.2f}<=MIN={min_low:.2f}, "
                            f"ADX={adx:.1f} RSI={rsi:.1f}) TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                        ),
                    )
                    return

            if self.allow_short and bar_high >= max_high and rsi >= self.rsi_short_min:
                if self.use_ema_filter and price < ema:
                    # downtrend에서만 rip 매도
                    self.tp_price = price - self.atr_tp_multiplier * atr
                    self.sl_price = price + self.atr_sl_multiplier * atr
                    self._bars_since_close = None
                    self._bars_in_position = 0
                    ctx.enter_short(
                        reason=(
                            f"Donchian Fade Short (H={bar_high:.2f}>=MAX={max_high:.2f}, "
                            f"ADX={adx:.1f} RSI={rsi:.1f} price<EMA={ema:.2f}) "
                            f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                        ),
                    )
                    return
                elif not self.use_ema_filter:
                    self.tp_price = price - self.atr_tp_multiplier * atr
                    self.sl_price = price + self.atr_sl_multiplier * atr
                    self._bars_since_close = None
                    self._bars_in_position = 0
                    ctx.enter_short(
                        reason=(
                            f"Donchian Fade Short (H={bar_high:.2f}>=MAX={max_high:.2f}, "
                            f"ADX={adx:.1f} RSI={rsi:.1f}) TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                        ),
                    )
                    return
            return

        # 정통 돌파: 롱은 상단 돌파 + EMA 위
        if self.allow_long and bar_high >= max_high:
            if self.use_ema_filter and price < ema:
                pass
            else:
                self.tp_price = price + self.atr_tp_multiplier * atr
                self.sl_price = price - self.atr_sl_multiplier * atr
                self._bars_since_close = None
                self._bars_in_position = 0
                ctx.enter_long(
                    reason=(
                        f"Donchian Long break (H={bar_high:.2f}>=MAX={max_high:.2f}, "
                        f"ADX={adx:.1f}>={self.adx_min:.0f}, EMA={ema:.2f}) "
                        f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                    ),
                )
                return

        # 숏: 봉 저가가 직전 도널칟 최저가를 터치/이탈 + EMA 아래
        if self.allow_short and bar_low <= min_low:
            if self.use_ema_filter and price > ema:
                return
            self.tp_price = price - self.atr_tp_multiplier * atr
            self.sl_price = price + self.atr_sl_multiplier * atr
            self._bars_since_close = None
            self._bars_in_position = 0
            ctx.enter_short(
                reason=(
                    f"Donchian Short break (L={bar_low:.2f}<=MIN={min_low:.2f}, "
                    f"ADX={adx:.1f}>={self.adx_min:.0f}, EMA={ema:.2f}) "
                    f"TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                ),
            )
            return
