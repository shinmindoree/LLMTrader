"""RSI 추세-풀백 LONG/SHORT 멀티-트리거 스캘퍼 (v6.0).

목적 / 위임 사항
----------------
- 사용자 위임:
    1) 1m~15m 단기 스캘핑, 하루 3회 이상 거래
    2) RSI 반드시 활용 (다른 지표는 자유)
    3) 어떤 백테스트 기간에서도 우상향 + 낮은 MDD
    4) 다양한 기간에 대한 과최적화 회피
- 이전 실험 요약:
    * 단순 RSI 평균회귀(BB+RSI extreme): -7~-15% (구조적 손실)
    * Donchian 채널 돌파 5m/15m: -10~-15% (5m breakouts are noise)
    * RSI 50-cross 추세추종: WR 24% → 대량 손실
    * 깊은 풀백(35→45) + 추세필터 (v3a): 35 trades / 8mo, 5/8 positive,
      MDD 1.13%, PF 2.43, avg_rtn -0.26% — 가장 안정적이지만 빈도 부족
- v6 설계: v3a 의 깊은 풀백 엣지를 유지하면서, 같은 추세 정렬 안에서
  여러 트리거 임계(45/55) 를 허용해 진입 빈도를 확장한다.

신호 규칙 (RSI 멀티-트리거 추세-풀백)
-------------------------------------
- 추세 정렬 (close vs EMA(trend_period), 옵션 slope 체크).
- 무장(armed) 조건:
    * require_pullback=1:
        - long_armed: long_bias 상태에서 RSI <= rsi_oversold 도달
        - short_armed: short_bias 상태에서 RSI >= rsi_overbought 도달
    * require_pullback=0:
        - long_armed = long_bias, short_armed = short_bias
- 진입 트리거 (모든 트리거가 같은 봉에서 발화하면 첫 발화로 진입):
    * Long: long_armed AND RSI cross_above 임계 (multi_trigger_levels 중 하나)
    * Short: short_armed AND RSI cross_below 임계
- 추가 필터:
    * ATR finite
    * ADX >= adx_min
    * 쿨다운 cooldown_bars
- 청산:
    * TP = entry ± ATR × atr_tp_multiplier
    * SL = entry ± ATR × atr_sl_multiplier
    * BE: entry ± entry_atr × breakeven_atr 도달 시 SL → entry
    * RSI 조기 청산: 롱 보유 중 RSI > long_exit_rsi / 숏 보유 중 RSI < short_exit_rsi
    * 시간 청산: bars_in_position >= max_hold_bars
- 청산 후 cooldown_bars 동안 신규 진입 금지.

웹 UI / 인프라
--------------
- 파일명/클래스명 `RsiLongShortStrategy` 는 web UI / runner / sweeper 가 의존하므로 유지.
- 모듈 레벨 `STRATEGY_PARAMS`, `STRATEGY_PARAM_SCHEMA` 노출.
"""

from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strategy.base import Strategy
from strategy.context import StrategyContext


# ---------------------------------------------------------------------------
# 기본 파라미터 (BTCUSDT 5m, 8개월 다기간 안정성 우선)
# ---------------------------------------------------------------------------
STRATEGY_PARAMS: dict[str, Any] = {
    # === RSI 코어 (v3a-best 디폴트) ===
    "rsi_period": 14,
    "rsi_oversold": 35.0,        # 풀백 무장 임계 (롱) — 깊은 풀백이 edge
    "rsi_overbought": 65.0,      # 풀백 무장 임계 (숏)
    # 멀티 트리거 — 쉼표 구분 또는 JSON 배열. 첫 발화로 진입.
    # 풀백 모드에서는 첫 트리거(45) 만 실효; 두 번째(55)는 require_pullback=0 일 때 의미.
    "long_trigger_levels": "45",
    "short_trigger_levels": "55",
    "long_exit_rsi": 80.0,       # 보유 중 롱 청산 (반전)
    "short_exit_rsi": 20.0,
    "require_pullback": 1,       # 0 → bias 만 충족하면 무장 (풀백 미요구)

    # === 추세 필터 ===
    "trend_period": 100,         # close vs EMA
    "slope_lookback": 5,
    "require_slope": 0,          # 1 → EMA 기울기 정렬 필요

    # === ADX 강도 필터 ===
    "adx_period": 14,
    "adx_min": 12.0,

    # === ATR 리스크 ===
    "atr_period": 14,
    "atr_tp_multiplier": 3.5,
    "atr_sl_multiplier": 2.5,    # R:R 1.4 (v3a 와 동일)
    "breakeven_atr": 1.2,        # 1.2×ATR 수익 시 SL → entry

    # === 보유/쿨다운 ===
    "max_hold_bars": 80,
    "cooldown_bars": 1,
}


STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "rsi_period": {"type": "integer", "min": 2, "max": 100, "label": "RSI 기간",
        "description": "RSI 계산 기간.", "group": "지표 (Indicator)"},
    "rsi_oversold": {"type": "number", "min": 1, "max": 99, "label": "RSI 과매도",
        "description": "롱 풀백 무장 임계.", "group": "진입 (Entry)"},
    "rsi_overbought": {"type": "number", "min": 1, "max": 99, "label": "RSI 과매수",
        "description": "숏 풀백 무장 임계.", "group": "진입 (Entry)"},
    "long_trigger_levels": {"type": "string", "label": "롱 트리거 레벨",
        "description": "RSI cross-up 트리거 레벨 (쉼표/JSON). 예: \"45,55\".", "group": "진입 (Entry)"},
    "short_trigger_levels": {"type": "string", "label": "숏 트리거 레벨",
        "description": "RSI cross-down 트리거 레벨 (쉼표/JSON). 예: \"55,45\".", "group": "진입 (Entry)"},
    "long_exit_rsi": {"type": "number", "min": 1, "max": 99, "label": "롱 조기 청산 RSI",
        "description": "롱 보유 중 RSI > 이 값이면 조기 청산.", "group": "청산 (Exit)"},
    "short_exit_rsi": {"type": "number", "min": 1, "max": 99, "label": "숏 조기 청산 RSI",
        "description": "숏 보유 중 RSI < 이 값이면 조기 청산.", "group": "청산 (Exit)"},
    "require_pullback": {"type": "integer", "min": 0, "max": 1, "label": "풀백 요구",
        "description": "0=bias 만으로 무장, 1=oversold/overbought 도달 필요.", "group": "진입 (Entry)"},
    "trend_period": {"type": "integer", "min": 5, "max": 1000, "label": "추세 EMA 기간",
        "description": "close vs EMA(이 값) 으로 long/short bias 결정.", "group": "필터 (Filter)"},
    "slope_lookback": {"type": "integer", "min": 1, "max": 100, "label": "EMA 기울기 lookback",
        "description": "EMA 기울기 비교 봉 수.", "group": "필터 (Filter)"},
    "require_slope": {"type": "integer", "min": 0, "max": 1, "label": "기울기 정렬 요구",
        "description": "1=EMA 기울기 부호도 일치해야 진입.", "group": "필터 (Filter)"},
    "adx_period": {"type": "integer", "min": 2, "max": 100, "label": "ADX 기간",
        "description": "ADX 계산 기간.", "group": "지표 (Indicator)"},
    "adx_min": {"type": "number", "min": 0, "max": 100, "label": "ADX 최소",
        "description": "ADX 가 이 값 이상이어야 진입.", "group": "필터 (Filter)"},
    "atr_period": {"type": "integer", "min": 2, "max": 100, "label": "ATR 기간",
        "description": "ATR 계산 기간.", "group": "지표 (Indicator)"},
    "atr_tp_multiplier": {"type": "number", "min": 0.1, "max": 20, "label": "TP ATR 배수",
        "description": "익절 거리 = entry ± ATR × 이 값.", "group": "청산 (Exit)"},
    "atr_sl_multiplier": {"type": "number", "min": 0.1, "max": 20, "label": "SL ATR 배수",
        "description": "손절 거리 = entry ± ATR × 이 값.", "group": "청산 (Exit)"},
    "breakeven_atr": {"type": "number", "min": 0, "max": 20, "label": "BE 이동 배수",
        "description": "이 배수 × ATR 수익 시 SL을 진입가로 이동.", "group": "청산 (Exit)"},
    "max_hold_bars": {"type": "integer", "min": 1, "max": 2000, "label": "최대 보유 봉",
        "description": "초과 시 강제 청산.", "group": "청산 (Exit)"},
    "cooldown_bars": {"type": "integer", "min": 0, "max": 500, "label": "재진입 쿨다운",
        "description": "청산 후 이 만큼 봉이 지나야 신규 진입.", "group": "진입 (Entry)"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def _value_at_offset(values: Any, offset_from_end: int) -> float | None:
    try:
        n = int(getattr(values, "size", len(values)))
    except Exception:  # noqa: BLE001
        return None
    idx = n - 1 - offset_from_end
    if idx < 0:
        return None
    try:
        v = float(values[idx])
    except Exception:  # noqa: BLE001
        return None
    if math.isnan(v):
        return None
    return v


def _register_talib_indicator(ctx: StrategyContext, name: str) -> None:
    """TA-Lib 인디케이터 등록 (slope_offset 커스텀 kwarg 지원).

    slope_offset=N 이면 가장 최근값이 아닌 N봉 전의 값을 반환 (기울기 계산용).
    """

    try:
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    _OHLCV = {"open", "high", "low", "close", "volume", "real"}

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        slope_offset = int(kwargs.pop("slope_offset", 0) or 0)
        output = kwargs.pop("output", None)
        kwargs.pop("output_index", None)
        price_source = kwargs.pop("price", None)
        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("builtin indicator params must be passed as keywords")
        if "period" in kwargs and "timeperiod" not in kwargs:
            kwargs["timeperiod"] = kwargs.pop("period")
        inputs_fn = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs_fn):
            return float("nan")
        raw = inputs_fn()
        prepared = {
            key: (np.asarray(list(values), dtype="float64") if not hasattr(values, "dtype") else values)
            for key, values in raw.items()
        }
        if "real" not in prepared and "close" in prepared:
            prepared["real"] = prepared["close"]
        if price_source is not None and price_source.lower() in _OHLCV:
            prepared["real"] = prepared.get(price_source.lower(), prepared.get("close"))
        fn = abstract.Function(name.strip().upper())
        result = fn(prepared, **kwargs)
        if isinstance(result, dict):
            target = result[output] if (output is not None and output in result) else list(result.values())[0]
        elif isinstance(result, (list, tuple)):
            target = result[0]
        else:
            target = result
        if slope_offset > 0:
            v = _value_at_offset(target, slope_offset)
        else:
            v = _last_non_nan(target)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


def _parse_levels(raw: Any) -> list[float]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    if isinstance(raw, (int, float)):
        return [float(raw)]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("["):
            import json
            try:
                arr = json.loads(s)
                return [float(x) for x in arr]
            except Exception:  # noqa: BLE001
                pass
        return [float(x.strip()) for x in s.split(",") if x.strip()]
    return []


# ---------------------------------------------------------------------------
# 전략 본체
# ---------------------------------------------------------------------------
class RsiLongShortStrategy(Strategy):
    """RSI 추세-풀백 멀티-트리거 LONG/SHORT 스캘퍼.

    파일명/클래스명은 인프라 의존성 (web UI, runner, sweeper) 으로 유지.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}

        # RSI
        self.rsi_period: int = int(p["rsi_period"])
        self.rsi_oversold: float = float(p["rsi_oversold"])
        self.rsi_overbought: float = float(p["rsi_overbought"])
        self.long_trigger_levels: list[float] = sorted(_parse_levels(p["long_trigger_levels"]))
        # short triggers cross DOWN, so we sort descending so we test high-first
        self.short_trigger_levels: list[float] = sorted(_parse_levels(p["short_trigger_levels"]), reverse=True)
        if not self.long_trigger_levels:
            self.long_trigger_levels = [45.0]
        if not self.short_trigger_levels:
            self.short_trigger_levels = [55.0]
        self.long_exit_rsi: float = float(p["long_exit_rsi"])
        self.short_exit_rsi: float = float(p["short_exit_rsi"])
        self.require_pullback: int = int(p["require_pullback"])

        # Trend / slope
        self.trend_period: int = int(p["trend_period"])
        self.slope_lookback: int = int(p["slope_lookback"])
        self.require_slope: int = int(p["require_slope"])

        # ADX
        self.adx_period: int = int(p["adx_period"])
        self.adx_min: float = float(p["adx_min"])

        # ATR
        self.atr_period: int = int(p["atr_period"])
        self.atr_tp_multiplier: float = float(p["atr_tp_multiplier"])
        self.atr_sl_multiplier: float = float(p["atr_sl_multiplier"])
        self.breakeven_atr: float = float(p["breakeven_atr"])

        # Hold / cooldown
        self.max_hold_bars: int = int(p["max_hold_bars"])
        self.cooldown_bars: int = int(p["cooldown_bars"])

        # 런타임 상태
        self._mode: str | None = None
        self._bars_since_close: int = 10**9
        self._bars_in_position: int = 0
        self._entry_price: float = 0.0
        self._entry_atr: float = 0.0
        self._stop_price: float = 0.0
        self._take_price: float = 0.0
        self._side: str | None = None
        self._long_armed: bool = False
        self._short_armed: bool = False
        self._prev_rsi: float = math.nan
        self._banner_emitted: bool = False
        self._last_bias: str | None = None

        self.params = dict(p)
        self.indicator_config = {}

    # ------------------------------------------------------------------ init
    def initialize(self, ctx: StrategyContext) -> None:
        ctx_cls = type(ctx).__name__
        ctx_module = type(ctx).__module__
        if "Backtest" in ctx_cls:
            self._mode = "backtest"
        elif (
            "Live" in ctx_cls
            or ctx_cls == "StreamBoundStrategyContext"
            or ctx_module.startswith("live.")
        ):
            self._mode = "live"
        else:
            self._mode = None

        _register_talib_indicator(ctx, "RSI")
        _register_talib_indicator(ctx, "ATR")
        _register_talib_indicator(ctx, "ADX")
        _register_talib_indicator(ctx, "EMA")

        # 상태 초기화
        self._bars_since_close = 10**9
        self._bars_in_position = 0
        self._entry_price = 0.0
        self._entry_atr = 0.0
        self._stop_price = 0.0
        self._take_price = 0.0
        self._side = None
        self._long_armed = False
        self._short_armed = False
        self._prev_rsi = math.nan
        self._last_bias = None

    # ------------------------------------------------------------------ helpers
    def _reset_position_state(self) -> None:
        self._bars_in_position = 0
        self._entry_price = 0.0
        self._entry_atr = 0.0
        self._stop_price = 0.0
        self._take_price = 0.0
        self._side = None

    def _maybe_breakeven_shift(self, last_price: float) -> None:
        if self.breakeven_atr <= 0 or self._side is None or self._entry_atr <= 0:
            return
        threshold = self._entry_atr * self.breakeven_atr
        if self._side == "LONG":
            if last_price - self._entry_price >= threshold and self._stop_price < self._entry_price:
                self._stop_price = self._entry_price
        else:  # SHORT
            if self._entry_price - last_price >= threshold and (self._stop_price > self._entry_price or self._stop_price == 0):
                self._stop_price = self._entry_price

    # ------------------------------------------------------------------ bar
    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:  # noqa: C901
        if not self._banner_emitted:
            try:
                print(
                    "🚀 [버전확인] RsiLongShortStrategy v6.0 "
                    "(RSI multi-trigger trend-pullback) 시작!",
                    flush=True,
                )
            except Exception:  # noqa: BLE001
                pass
            self._banner_emitted = True

        # 무포지션 정리 (이전 봉에서 청산되었을 수 있음)
        if ctx.position_size == 0:
            if self._side is not None:
                self._reset_position_state()
                self._bars_since_close = 0

        last_price = float(bar.get("price", bar.get("close", 0.0)) or 0.0)
        if not math.isfinite(last_price) or last_price <= 0:
            return

        # 매 틱: BE 시프트 + TP/SL 체크 (보유 중)
        if ctx.position_size != 0 and self._side is not None:
            self._maybe_breakeven_shift(last_price)
            if self._side == "LONG":
                if self._take_price > 0 and last_price >= self._take_price:
                    ctx.close_position(reason=f"v6: TP +{self.atr_tp_multiplier}xATR")
                    return
                if self._stop_price > 0 and last_price <= self._stop_price:
                    ctx.close_position(reason=f"v6: SL -{self.atr_sl_multiplier}xATR")
                    return
            else:  # SHORT
                if self._take_price > 0 and last_price <= self._take_price:
                    ctx.close_position(reason=f"v6: TP +{self.atr_tp_multiplier}xATR")
                    return
                if self._stop_price > 0 and last_price >= self._stop_price:
                    ctx.close_position(reason=f"v6: SL -{self.atr_sl_multiplier}xATR")
                    return

        # 새 봉에서만 의사결정
        is_new_bar = bool(bar.get("is_new_bar", True))
        if not is_new_bar:
            return

        # 카운터
        if ctx.position_size != 0:
            self._bars_in_position += 1
        else:
            self._bars_since_close += 1

        # 봉 종가 (bar 의 close 사용; 없으면 last_price)
        close_price = float(bar.get("close", last_price) or last_price)

        # 지표
        try:
            rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        except Exception:  # noqa: BLE001
            rsi = math.nan
        try:
            atr = float(ctx.get_indicator("ATR", period=self.atr_period))
        except Exception:  # noqa: BLE001
            atr = math.nan
        try:
            adx = float(ctx.get_indicator("ADX", period=self.adx_period))
        except Exception:  # noqa: BLE001
            adx = math.nan
        try:
            ema = float(ctx.get_indicator("EMA", period=self.trend_period))
        except Exception:  # noqa: BLE001
            ema = math.nan
        ema_prev = math.nan
        if self.require_slope and self.slope_lookback > 0:
            try:
                ema_prev = float(ctx.get_indicator(
                    "EMA", period=self.trend_period, slope_offset=self.slope_lookback,
                ))
            except Exception:  # noqa: BLE001
                ema_prev = math.nan

        # 추세 bias
        long_bias = False
        short_bias = False
        if not math.isnan(ema):
            long_bias = close_price > ema
            short_bias = close_price < ema
            if self.require_slope and not math.isnan(ema_prev):
                slope_up = ema > ema_prev
                slope_dn = ema < ema_prev
                long_bias = long_bias and slope_up
                short_bias = short_bias and slope_dn
        current_bias = "LONG" if long_bias else ("SHORT" if short_bias else None)

        # 보유 중: 시간/RSI 조기 청산
        if ctx.position_size != 0 and self._side is not None:
            if self._bars_in_position >= self.max_hold_bars:
                ctx.close_position(reason="v6: TIME_EXIT")
                return
            if (
                self._side == "LONG"
                and not math.isnan(rsi)
                and rsi >= self.long_exit_rsi
            ):
                ctx.close_position(reason="v6: RSI_EXIT_LONG")
                return
            if (
                self._side == "SHORT"
                and not math.isnan(rsi)
                and rsi <= self.short_exit_rsi
            ):
                ctx.close_position(reason="v6: RSI_EXIT_SHORT")
                return
            self._prev_rsi = rsi if not math.isnan(rsi) else self._prev_rsi
            return

        # === 무포지션 — 무장 / 트리거 판정 ===

        # bias 가 반대편으로 가면 반대편 arm 만 클리어
        if current_bias == "LONG":
            self._short_armed = False
        elif current_bias == "SHORT":
            self._long_armed = False
        self._last_bias = current_bias

        if not math.isnan(rsi):
            if self.require_pullback:
                # v3a 호환: bias 가 정렬된 상태에서만 oversold/overbought 도달을
                # arm 으로 기록 (반대 사이드 풀백이 다음 추세 진입 신호를 오염시키지 않게).
                if long_bias and rsi <= self.rsi_oversold:
                    self._long_armed = True
                if short_bias and rsi >= self.rsi_overbought:
                    self._short_armed = True
            else:
                self._long_armed = long_bias
                self._short_armed = short_bias

        # 진입 게이트
        if self._bars_since_close < self.cooldown_bars:
            self._prev_rsi = rsi if not math.isnan(rsi) else self._prev_rsi
            return
        if math.isnan(atr) or atr <= 0:
            self._prev_rsi = rsi if not math.isnan(rsi) else self._prev_rsi
            return
        if not math.isnan(self.adx_min) and (math.isnan(adx) or adx < self.adx_min):
            self._prev_rsi = rsi if not math.isnan(rsi) else self._prev_rsi
            return
        if math.isnan(rsi) or math.isnan(self._prev_rsi):
            self._prev_rsi = rsi if not math.isnan(rsi) else self._prev_rsi
            return

        prev_rsi = self._prev_rsi

        # 롱 멀티 트리거: 어느 하나라도 cross-up 이면 진입
        if long_bias and self._long_armed:
            for level in self.long_trigger_levels:
                if prev_rsi < level <= rsi:
                    self._open_long(ctx, close_price, atr, level)
                    return

        # 숏 멀티 트리거: 어느 하나라도 cross-down 이면 진입
        if short_bias and self._short_armed:
            for level in self.short_trigger_levels:
                if prev_rsi > level >= rsi:
                    self._open_short(ctx, close_price, atr, level)
                    return

        self._prev_rsi = rsi

    # ------------------------------------------------------------------ entry helpers
    def _open_long(
        self, ctx: StrategyContext, price: float, atr: float, level: float,
    ) -> None:
        try:
            ctx.enter_long(reason=f"v6 LONG cross↑{level:.0f} RSI")
        except Exception:  # noqa: BLE001
            return
        self._side = "LONG"
        self._entry_price = price
        self._entry_atr = atr
        self._take_price = price + atr * self.atr_tp_multiplier
        self._stop_price = price - atr * self.atr_sl_multiplier
        self._bars_in_position = 0
        self._long_armed = False
        self._short_armed = False

    def _open_short(
        self, ctx: StrategyContext, price: float, atr: float, level: float,
    ) -> None:
        try:
            ctx.enter_short(reason=f"v6 SHORT cross↓{level:.0f} RSI")
        except Exception:  # noqa: BLE001
            return
        self._side = "SHORT"
        self._entry_price = price
        self._entry_atr = atr
        self._take_price = price - atr * self.atr_tp_multiplier
        self._stop_price = price + atr * self.atr_sl_multiplier
        self._bars_in_position = 0
        self._long_armed = False
        self._short_armed = False
