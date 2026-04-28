from __future__ import annotations

import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


STRATEGY_PARAMS: dict[str, Any] = {
    "ema_fast_period": 20,
    "ema_slow_period": 50,
    "rsi_period": 14,
    "atr_period": 14,
    "pullback_atr_mult": 0.4,
    "long_reclaim_buffer_pct": 0.0005,
    "short_reject_buffer_pct": 0.0005,
    "rsi_long_min": 45.0,
    "rsi_short_max": 55.0,
    "atr_stop_mult": 1.5,
    "atr_take_mult": 2.4,
    "entry_pct": 0.95,
}

STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "ema_fast_period": {"type": "integer", "min": 5, "max": 100, "label": "빠른 EMA 기간", "description": "단기 추세 판단 기간입니다.", "group": "지표 (Indicator)"},
    "ema_slow_period": {"type": "integer", "min": 10, "max": 300, "label": "느린 EMA 기간", "description": "중기 추세 기준선 기간입니다.", "group": "지표 (Indicator)"},
    "rsi_period": {"type": "integer", "min": 2, "max": 100, "label": "RSI 기간", "description": "모멘텀 필터 계산 기간입니다.", "group": "지표 (Indicator)"},
    "atr_period": {"type": "integer", "min": 2, "max": 100, "label": "ATR 기간", "description": "변동성 측정 기간입니다.", "group": "지표 (Indicator)"},
    "pullback_atr_mult": {"type": "number", "min": 0.1, "max": 5.0, "label": "눌림 ATR 배수", "description": "직전 봉이 EMA 대비 이 배수만큼 눌렸는지 판정합니다. 작을수록 더 자주 진입합니다.", "group": "진입 (Entry)"},
    "long_reclaim_buffer_pct": {"type": "number", "min": 0.0, "max": 0.01, "label": "롱 재돌파 버퍼", "description": "롱 진입용 EMA 재돌파 여유값입니다.", "group": "진입 (Entry)"},
    "short_reject_buffer_pct": {"type": "number", "min": 0.0, "max": 0.01, "label": "숏 재이탈 버퍼", "description": "숏 진입용 EMA 재이탈 여유값입니다.", "group": "진입 (Entry)"},
    "rsi_long_min": {"type": "number", "min": 1, "max": 99, "label": "롱 RSI 하한", "description": "롱 진입 시 필요한 최소 RSI입니다.", "group": "진입 (Entry)"},
    "rsi_short_max": {"type": "number", "min": 1, "max": 99, "label": "숏 RSI 상한", "description": "숏 진입 시 허용 최대 RSI입니다.", "group": "진입 (Entry)"},
    "atr_stop_mult": {"type": "number", "min": 0.2, "max": 10.0, "label": "손절 ATR 배수", "description": "ATR 기반 손절 거리 배수입니다.", "group": "리스크 관리 (Risk)"},
    "atr_take_mult": {"type": "number", "min": 0.2, "max": 15.0, "label": "익절 ATR 배수", "description": "ATR 기반 익절 거리 배수입니다.", "group": "청산 (Exit)"},
    "entry_pct": {"type": "number", "min": 0.01, "max": 1.0, "label": "진입 비중", "description": "진입 시 자본 사용 비중입니다.", "group": "일반 (General)"},
}


def _last_non_nan(values: Any) -> float | None:
    try:
        n = int(len(values))
    except Exception:
        return None
    for i in range(n - 1, -1, -1):
        try:
            v = float(values[i])
        except Exception:
            continue
        if not math.isnan(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, name: str) -> None:
    return


def crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    return current <= level < prev


class BtcPullbackLongShortStrategy(Strategy):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        merged = {**STRATEGY_PARAMS, **kwargs}
        self.ema_fast_period = int(merged["ema_fast_period"])
        self.ema_slow_period = int(merged["ema_slow_period"])
        self.rsi_period = int(merged["rsi_period"])
        self.atr_period = int(merged["atr_period"])
        self.pullback_atr_mult = float(merged["pullback_atr_mult"])
        self.long_reclaim_buffer_pct = float(merged["long_reclaim_buffer_pct"])
        self.short_reject_buffer_pct = float(merged["short_reject_buffer_pct"])
        self.rsi_long_min = float(merged["rsi_long_min"])
        self.rsi_short_max = float(merged["rsi_short_max"])
        self.atr_stop_mult = float(merged["atr_stop_mult"])
        self.atr_take_mult = float(merged["atr_take_mult"])
        self.entry_pct = float(merged["entry_pct"])

        self.prev_close: float | None = None
        self.is_closing = False
        self.long_stop_price: float | None = None
        self.long_take_price: float | None = None
        self.short_stop_price: float | None = None
        self.short_take_price: float | None = None

        self.params = dict(merged)
        self.indicator_config = {
            "EMA": {"period": self.ema_fast_period},
            "RSI": {"period": self.rsi_period},
            "ATR": {"period": self.atr_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "EMA")
        register_talib_indicator_all_outputs(ctx, "RSI")
        register_talib_indicator_all_outputs(ctx, "ATR")
        self.prev_close = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            self.is_closing = False
            self.long_stop_price = None
            self.long_take_price = None
            self.short_stop_price = None
            self.short_take_price = None

        if ctx.get_open_orders():
            return
        if not bool(bar.get("is_new_bar", True)):
            return

        close = ctx.current_price
        price = ctx.current_price
        open_ = float(bar.get("open", close))
        high = float(bar.get("high", close))
        low = float(bar.get("low", close))
        volume = float(bar.get("volume", 0))

        ema_fast = float(ctx.get_indicator("EMA", period=self.ema_fast_period))
        ema_slow = float(ctx.get_indicator("EMA", period=self.ema_slow_period))
        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))

        if not (math.isfinite(ema_fast) and math.isfinite(ema_slow) and math.isfinite(rsi) and math.isfinite(atr)):
            return
        if self.prev_close is None:
            self.prev_close = close
            return

        if ctx.position_size > 0 and not self.is_closing:
            entry = float(ctx.position_entry_price)
            if self.long_stop_price is None:
                self.long_stop_price = entry - atr * self.atr_stop_mult
                self.long_take_price = entry + atr * self.atr_take_mult
            if close <= float(self.long_stop_price) or close >= float(self.long_take_price) or ema_fast < ema_slow:
                self.is_closing = True
                ctx.close_position(reason="Long exit", exit_reason="Long exit")
                self.prev_close = close
                return

        if ctx.position_size < 0 and not self.is_closing:
            entry = float(ctx.position_entry_price)
            if self.short_stop_price is None:
                self.short_stop_price = entry + atr * self.atr_stop_mult
                self.short_take_price = entry - atr * self.atr_take_mult
            if close >= float(self.short_stop_price) or close <= float(self.short_take_price) or ema_fast > ema_slow:
                self.is_closing = True
                ctx.close_position(reason="Short exit", exit_reason="Short exit")
                self.prev_close = close
                return

        long_reclaim_level = ema_fast * (1.0 + self.long_reclaim_buffer_pct)
        short_reject_level = ema_fast * (1.0 - self.short_reject_buffer_pct)

        # Pullback judged on PREVIOUS bar; reclaim/reject judged on CURRENT bar.
        long_trigger = (
            ema_fast > ema_slow
            and self.prev_close <= (ema_fast - atr * self.pullback_atr_mult)
            and crossed_above(self.prev_close, close, long_reclaim_level)
            and rsi >= self.rsi_long_min
        )
        short_trigger = (
            ema_fast < ema_slow
            and self.prev_close >= (ema_fast + atr * self.pullback_atr_mult)
            and crossed_below(self.prev_close, close, short_reject_level)
            and rsi <= self.rsi_short_max
        )

        if ctx.position_size == 0:
            if long_trigger:
                ctx.enter_long(reason="Pullback long", entry_pct=self.entry_pct)
            elif short_trigger:
                ctx.enter_short(reason="Pullback short", entry_pct=self.entry_pct)

        _ = (price, open_, high, low, volume)
        self.prev_close = close
