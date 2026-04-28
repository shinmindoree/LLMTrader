"""RSI 기반 롱/숏 전략 (1분봉 튜닝판).

목적:
- BTCUSDT 1분봉 + 약 10회/일 매매 빈도(30일 ≈ 300회)에서 꾸준히 우상향 가능하도록
  순수 RSI 크로스 전략에 추세필터(EMA), ATR 기반 TP/SL, 쿨다운을 추가.

신호 규칙:
- 롱 진입: RSI(rsi_period)가 long_entry_rsi 상향 돌파 AND close > EMA(trend_period)
- 숏 진입: RSI(rsi_period)가 short_entry_rsi 하향 돌파 AND close < EMA(trend_period)
- 청산:
    * TP: ATR(atr_period) × atr_tp_multiplier 도달
    * SL: ATR(atr_period) × atr_sl_multiplier (시스템 stop_loss_pct도 별도로 작동)
    * RSI 반대 크로스(long_exit_rsi 상향 / short_exit_rsi 하향) 도달
    * max_hold_bars 초과 시 시간 만료
- 청산 후 cooldown_bars 동안 신규 진입 금지

참고:
- 수량/리스크는 시스템(Context/Risk)에서 처리.
- 새 봉(is_new_bar=True)에서만 RSI 크로스 판단/prev_rsi 갱신.
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


def register_talib_indicator(ctx: StrategyContext, name: str) -> None:
    """TA-Lib builtin 인디케이터 등록 (마지막 non-nan 값을 float로 반환)."""

    try:
        import numpy as np  # type: ignore
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    _OHLCV_KEYS = {"open", "high", "low", "close", "volume", "real"}

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        kwargs.pop("output_index", None)
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
        if price_source is not None and price_source.lower() in _OHLCV_KEYS:
            prepared_inputs["real"] = prepared_inputs.get(
                price_source.lower(), prepared_inputs.get("close")
            )

        normalized = name.strip().upper()
        fn = abstract.Function(normalized)
        result = fn(prepared_inputs, **kwargs)

        if isinstance(result, dict):
            if output is not None and output in result:
                v = _last_non_nan(result[output])
            else:
                v = _last_non_nan(list(result.values())[0])
        elif isinstance(result, (list, tuple)):
            v = _last_non_nan(result[0])
        else:
            v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


# 웹 UI 파라미터 패널이 이 dict를 읽고 AST로 안전하게 갱신합니다.
STRATEGY_PARAMS: dict[str, Any] = {
    # 1m BTCUSDT 30일(2026-03-30 ~ 2026-04-29, +13.8% 강세장) 튜닝 결과
    # ~3회/일, win_rate ~42%, gross PnL ≈ flat. 횡보 구간 평균회귀 노림.
    "rsi_period": 7,
    "long_entry_rsi": 22.0,
    "long_exit_rsi": 95.0,       # ATR/시간 청산 우선 → RSI 역크로스 사실상 비활성
    "short_entry_rsi": 78.0,
    "short_exit_rsi": 5.0,
    "trend_period": 0,            # 0이면 EMA 추세 필터 비활성
    "adx_period": 14,
    "adx_max": 25.0,              # ADX < adx_max (횡보장)에서만 진입
    "bb_period": 20,
    "bb_stddev": 2.2,             # close가 BB 확장 이탈일 때만 (RSI + BB 컨플루언스)
    "use_bb_filter": 1,
    "use_bb_middle_tp": 0,
    "atr_period": 14,
    "atr_tp_multiplier": 2.5,
    "atr_sl_multiplier": 1.8,
    "breakeven_atr": 0.0,
    "max_hold_bars": 30,
    "cooldown_bars": 8,
}

STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "rsi_period": {"type": "integer", "min": 2, "max": 100, "label": "RSI 기간",
        "description": "RSI 계산 기간. 작을수록 민감.", "group": "지표 (Indicator)"},
    "long_entry_rsi": {"type": "number", "min": 1, "max": 99, "label": "롱 진입 RSI",
        "description": "RSI 상향 돌파 시 롱 진입.", "group": "진입 (Entry)"},
    "long_exit_rsi": {"type": "number", "min": 1, "max": 99, "label": "롱 청산 RSI",
        "description": "RSI 상향 돌파 시 롱 청산.", "group": "청산 (Exit)"},
    "short_entry_rsi": {"type": "number", "min": 1, "max": 99, "label": "숏 진입 RSI",
        "description": "RSI 하향 돌파 시 숏 진입.", "group": "진입 (Entry)"},
    "short_exit_rsi": {"type": "number", "min": 1, "max": 99, "label": "숏 청산 RSI",
        "description": "RSI 하향 돌파 시 숏 청산.", "group": "청산 (Exit)"},
    "trend_period": {"type": "integer", "min": 10, "max": 1000, "label": "추세 EMA 기간",
        "description": "close > EMA면 롱만, close < EMA면 숏만 허용.", "group": "필터 (Filter)"},
    "atr_period": {"type": "integer", "min": 2, "max": 100, "label": "ATR 기간",
        "description": "ATR 계산 기간 (TP/SL용).", "group": "지표 (Indicator)"},
    "atr_tp_multiplier": {"type": "number", "min": 0.1, "max": 10.0, "label": "ATR TP 배수",
        "description": "진입가 ± ATR × 배수에서 익절.", "group": "청산 (Exit)"},
    "atr_sl_multiplier": {"type": "number", "min": 0.1, "max": 10.0, "label": "ATR SL 배수",
        "description": "진입가 ± ATR × 배수에서 손절.", "group": "청산 (Exit)"},
    "max_hold_bars": {"type": "integer", "min": 1, "max": 1000, "label": "최대 보유 봉 수",
        "description": "초과 시 강제 청산.", "group": "청산 (Exit)"},
    "cooldown_bars": {"type": "integer", "min": 0, "max": 1000, "label": "쿨다운 봉 수",
        "description": "청산 직후 N봉 동안 신규 진입 금지.", "group": "필터 (Filter)"},
}


def crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    return current <= level < prev


class RsiLongShortStrategy(Strategy):
    """RSI + EMA 추세필터 + ATR TP/SL 롱/숏 전략 (1분봉 튜닝판)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}
        rsi_period = int(p["rsi_period"])
        long_entry_rsi = float(p["long_entry_rsi"])
        long_exit_rsi = float(p["long_exit_rsi"])
        short_entry_rsi = float(p["short_entry_rsi"])
        short_exit_rsi = float(p["short_exit_rsi"])

        if not (0 < long_entry_rsi < long_exit_rsi < 100):
            raise ValueError("invalid long RSI thresholds")
        if not (0 < short_exit_rsi < short_entry_rsi < 100):
            raise ValueError("invalid short RSI thresholds")
        if rsi_period <= 1:
            raise ValueError("rsi_period must be > 1")

        self.rsi_period = rsi_period
        self.long_entry_rsi = long_entry_rsi
        self.long_exit_rsi = long_exit_rsi
        self.short_entry_rsi = short_entry_rsi
        self.short_exit_rsi = short_exit_rsi
        self.trend_period = int(p["trend_period"])
        self.adx_period = int(p["adx_period"])
        self.adx_max = float(p["adx_max"])
        self.bb_period = int(p["bb_period"])
        self.bb_stddev = float(p["bb_stddev"])
        self.use_bb_filter = bool(int(p["use_bb_filter"]))
        self.use_bb_middle_tp = bool(int(p.get("use_bb_middle_tp", 0)))
        self.atr_period = int(p["atr_period"])
        self.atr_tp_multiplier = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier = float(p["atr_sl_multiplier"])
        self.breakeven_atr = float(p.get("breakeven_atr", 0.0))
        self.max_hold_bars = int(p["max_hold_bars"])
        self.cooldown_bars = int(p["cooldown_bars"])

        self.prev_rsi: float | None = None
        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self.entry_price: float = 0.0
        self.entry_atr: float = 0.0
        self.breakeven_done: bool = False
        self._bars_in_position: int = 0
        self._bars_since_close: int | None = None

        self.params = dict(p)
        cfg: dict[str, Any] = {
            "RSI": {"period": self.rsi_period},
            "ATR": {"period": self.atr_period},
            "ADX": {"period": self.adx_period},
        }
        if self.trend_period > 0:
            cfg["EMA"] = {"period": self.trend_period}
        if self.use_bb_filter:
            cfg["BBANDS"] = {"period": self.bb_period, "nbdevup": self.bb_stddev, "nbdevdn": self.bb_stddev}
        self.indicator_config = cfg

    def initialize(self, ctx: StrategyContext) -> None:
        print("🚀 [버전확인] RsiLongShortStrategy v2.0 (1m tuned) 시작!")
        register_talib_indicator(ctx, "RSI")
        if self.trend_period > 0:
            register_talib_indicator(ctx, "EMA")
        register_talib_indicator(ctx, "ATR")
        register_talib_indicator(ctx, "ADX")
        if self.use_bb_filter:
            register_talib_indicator(ctx, "BBANDS")
        self.prev_rsi = None
        self.is_closing = False
        self.tp_price = 0.0
        self.sl_price = 0.0
        self.entry_price = 0.0
        self.entry_atr = 0.0
        self.breakeven_done = False
        self._bars_in_position = 0
        self._bars_since_close = None

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        if ctx.position_size == 0:
            if self.is_closing:
                self._bars_since_close = 0
            self.is_closing = False
            self._bars_in_position = 0
            self.tp_price = 0.0
            self.sl_price = 0.0
            self.entry_price = 0.0
            self.entry_atr = 0.0
            self.breakeven_done = False

        if ctx.get_open_orders():
            return

        # ===== 즉시 TP/SL 평가 =====
        if ctx.position_size != 0 and not self.is_closing:
            price = float(ctx.current_price)
            # 브레이크이벤 시프트: 가격이 entry+breakeven_atr*ATR 도달 시 SL을 진입가로 이동
            if (
                self.breakeven_atr > 0
                and not self.breakeven_done
                and self.entry_atr > 0
                and self.entry_price > 0
            ):
                trigger = self.entry_atr * self.breakeven_atr
                if ctx.position_size > 0 and price >= self.entry_price + trigger:
                    self.sl_price = max(self.sl_price, self.entry_price)
                    self.breakeven_done = True
                elif ctx.position_size < 0 and price <= self.entry_price - trigger:
                    self.sl_price = min(self.sl_price, self.entry_price) if self.sl_price > 0 else self.entry_price
                    self.breakeven_done = True
            if ctx.position_size > 0:
                if self.tp_price > 0 and price >= self.tp_price:
                    self.is_closing = True
                    ctx.close_position(reason=f"ATR TP Long {price:.2f}>={self.tp_price:.2f}", exit_reason="TAKE_PROFIT")
                    return
                if self.sl_price > 0 and price <= self.sl_price:
                    self.is_closing = True
                    ctx.close_position(reason=f"ATR SL Long {price:.2f}<={self.sl_price:.2f}", exit_reason="STOP_LOSS")
                    return
            else:
                if self.tp_price > 0 and price <= self.tp_price:
                    self.is_closing = True
                    ctx.close_position(reason=f"ATR TP Short {price:.2f}<={self.tp_price:.2f}", exit_reason="TAKE_PROFIT")
                    return
                if self.sl_price > 0 and price >= self.sl_price:
                    self.is_closing = True
                    ctx.close_position(reason=f"ATR SL Short {price:.2f}>={self.sl_price:.2f}", exit_reason="STOP_LOSS")
                    return

        if not bool(bar.get("is_new_bar", True)):
            return

        if ctx.position_size != 0:
            self._bars_in_position += 1
        if self._bars_since_close is not None:
            self._bars_since_close += 1

        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        if not math.isfinite(rsi):
            return

        prev_rsi = self.prev_rsi
        self.prev_rsi = rsi
        if prev_rsi is None or not math.isfinite(prev_rsi):
            return

        # ===== 시간 만료 청산 =====
        if ctx.position_size != 0 and not self.is_closing:
            if self._bars_in_position >= self.max_hold_bars:
                self.is_closing = True
                ctx.close_position(reason=f"Time Exit ({self._bars_in_position}b)")
                return

        # ===== RSI 반대 크로스 청산 =====
        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(prev_rsi, rsi, self.long_exit_rsi):
                self.is_closing = True
                ctx.close_position(reason=f"RSI Exit Long ({prev_rsi:.1f}->{rsi:.1f})")
                return
        if ctx.position_size < 0 and not self.is_closing:
            if crossed_below(prev_rsi, rsi, self.short_exit_rsi):
                self.is_closing = True
                ctx.close_position(reason=f"RSI Exit Short ({prev_rsi:.1f}->{rsi:.1f})")
                return

        # ===== 신규 진입 =====
        if ctx.position_size != 0:
            return
        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            return

        try:
            atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        except Exception:  # noqa: BLE001
            atr = math.nan
        try:
            adx = float(ctx.get_indicator("ADX", period=self.adx_period))
        except Exception:  # noqa: BLE001
            adx = math.nan
        price = float(ctx.current_price)
        if not (math.isfinite(atr) and atr > 0):
            return

        # ADX 필터: 추세 강하면 스킵 (평균회귀 함정 회피)
        if math.isfinite(adx) and adx >= self.adx_max:
            return

        ema: float = math.nan
        if self.trend_period > 0:
            try:
                ema = float(ctx.get_indicator("EMA", period=self.trend_period))
            except Exception:  # noqa: BLE001
                ema = math.nan
            if not math.isfinite(ema):
                return

        long_ok = (self.trend_period <= 0) or (price > ema)
        short_ok = (self.trend_period <= 0) or (price < ema)

        # BB 확장 이탈 필터 (평균회귀 신호 강화)
        bb_mid = math.nan
        if self.use_bb_filter:
            try:
                upper = float(ctx.get_indicator("BBANDS", period=self.bb_period,
                                                 nbdevup=self.bb_stddev, nbdevdn=self.bb_stddev,
                                                 output="upperband"))
                lower = float(ctx.get_indicator("BBANDS", period=self.bb_period,
                                                 nbdevup=self.bb_stddev, nbdevdn=self.bb_stddev,
                                                 output="lowerband"))
                bb_mid = float(ctx.get_indicator("BBANDS", period=self.bb_period,
                                                 nbdevup=self.bb_stddev, nbdevdn=self.bb_stddev,
                                                 output="middleband"))
            except Exception:  # noqa: BLE001
                upper = lower = math.nan
            if not (math.isfinite(upper) and math.isfinite(lower)):
                return
            long_ok = long_ok and (price < lower)
            short_ok = short_ok and (price > upper)

        if long_ok and crossed_above(prev_rsi, rsi, self.long_entry_rsi):
            tp = price + atr * self.atr_tp_multiplier
            if self.use_bb_middle_tp and self.use_bb_filter and math.isfinite(bb_mid):
                # BB middle이 TP보다 먼저 닿으면 그걸 사용 (더 비튼 타격)
                if bb_mid > price:
                    tp = min(tp, bb_mid)
            self.tp_price = tp
            self.sl_price = price - atr * self.atr_sl_multiplier
            self.entry_price = price
            self.entry_atr = atr
            self.breakeven_done = False
            ctx.enter_long(reason=f"Entry Long RSI {prev_rsi:.1f}->{rsi:.1f}")
            return

        if short_ok and crossed_below(prev_rsi, rsi, self.short_entry_rsi):
            tp = price - atr * self.atr_tp_multiplier
            if self.use_bb_middle_tp and self.use_bb_filter and math.isfinite(bb_mid):
                if bb_mid < price:
                    tp = max(tp, bb_mid)
            self.tp_price = tp
            self.sl_price = price + atr * self.atr_sl_multiplier
            self.entry_price = price
            self.entry_atr = atr
            self.breakeven_done = False
            ctx.enter_short(reason=f"Entry Short RSI {prev_rsi:.1f}->{rsi:.1f}")
            return
