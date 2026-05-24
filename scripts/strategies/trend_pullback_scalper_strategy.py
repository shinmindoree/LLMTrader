"""Trend-Aligned Pullback Scalper (TAPS) — 1m~5m 고빈도 저-MDD 전략.

설계 목표:
- 1~5분봉에서 거래 빈도가 충분히 높을 것 (수십~수백 trades/일이 아니라
  데이터 기간 대비 수백~수천 단위)
- MDD는 가능한 낮을 것
- 자산이 꾸준히 우상향할 것 (월/년 단위 손실월 최소화)

기본 아이디어 (`macd_hist_immediate_entry_takeprofit_strategy.py`에서 진화):
- "항상 in-the-market" 방식은 횡보장에서 0이 되는 신호 부근에서 부호가
  지속적으로 뒤집히며 수수료/슬리피지로 누적 손실이 발생한다.
- 대신 "추세 필터 + 추세 방향 풀백 진입 + ATR 스케일 TP/SL"의 고전적인
  스캘퍼 구조로 바꾼다. 각 트레이드는 명확한 우위(R = TP/SL 비율, 추세
  방향의 평균회귀)를 갖도록 한다.

진입 규칙 (모두 만족 시 진입):
- LONG
  1. EMA(fast) > EMA(slow) × (1 + trend_strength_pct)  — 추세 마진
  2. RSI(period) 이 rsi_long_level 을 상향 돌파 (oversold 반등)
  3. close < EMA(fast)  — 빠른 EMA 아래의 풀백 상태
  4. ATR%(=ATR/close) ∈ [min_atr_pct, max_atr_pct]  — 변동성 정상 범위
  5. cooldown_bars 동안 진입 금지

- SHORT (대칭)

청산 규칙 (먼저 트리거되는 것):
- TP : entry ± ATR × atr_tp_mult
- SL : entry ∓ ATR × atr_sl_mult
- Time-stop : max_hold_bars 봉 보유 시 시장가 청산
- (옵션) 추세 반전 시 즉시 청산: opposite_exit_enabled=True 이면
  EMA(fast)가 EMA(slow)를 반대 방향으로 교차하면 시장가 청산

리스크/거래 관리:
- 새 봉 확정에서만 진입 의사결정 (`is_new_bar=True`); 인트라바 노이즈 회피
- 청산은 매 가격 업데이트(SL/TP 시뮬레이션)에서 평가되므로 백테스트와
  라이브 모두에서 자연스럽게 동작
- 시스템 StopLoss(`stop_loss_pct`)는 그대로 운영. 전략의 atr_sl_mult 가
  먼저 트리거되도록 일반적으로 더 타이트하게 잡는다.

이 파일은 `macd_hist_immediate_entry_takeprofit_strategy.py` 와 독립적으로
존재한다 (원본 파일은 보존).
"""

from __future__ import annotations

import importlib
import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


# ---------------------------------------------------------------------------
# 공용 indicator helper (다른 스캘퍼 전략과 동일 패턴)
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


def register_talib_indicator_all_outputs(ctx: StrategyContext, name: str) -> None:
    """TA-Lib builtin indicator를 dict (multi-output) 또는 float 로 반환하도록 표준화."""
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


def _crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current


def _crossed_below(prev: float, current: float, level: float) -> bool:
    return current <= level < prev


# ---------------------------------------------------------------------------
# 파라미터
# ---------------------------------------------------------------------------

STRATEGY_PARAMS: dict[str, Any] = {
    # 추세 필터
    "ema_fast": 21,
    "ema_slow": 100,
    "trend_strength_pct": 0.0005,   # ema_fast가 ema_slow보다 최소 0.05% 위/아래여야 추세 인정

    # 진입 모드
    #   "pullback" : RSI 풀백 + EMA 위/아래 (기존 동작)
    #   "breakout" : Donchian high/low 돌파 + 추세 일치 (모멘텀 컨티뉴에이션)
    "entry_mode": "pullback",
    "donchian_period": 20,           # entry_mode=="breakout" 일 때 사용

    # 풀백 트리거 (RSI) — entry_mode=="pullback" 일 때만 사용
    "rsi_period": 14,
    "rsi_long_level": 40.0,         # RSI가 이 값 상향 돌파 → 롱 풀백 반등
    "rsi_short_level": 60.0,        # RSI가 이 값 하향 돌파 → 숏 풀백 반락

    # 변동성 게이트 (ATR%)
    "atr_period": 14,
    "min_atr_pct": 0.0003,          # 0.03% 미만은 너무 잔잔 (수수료 못 이김)
    "max_atr_pct": 0.0150,          # 1.5% 초과는 패닉 구간 (스파이크 위험)

    # ATR 스케일 청산
    "atr_tp_mult": 1.2,
    "atr_sl_mult": 0.8,             # SL이 TP보다 타이트 → RR 1.5
    "max_hold_bars": 24,            # 24 캔들 (5m → 2시간)

    # 트레일링 스톱 (>0 이면 활성화). 진입 후 가격이 ATR × trail_activation_mult
    # 이상 호의적으로 움직이면 SL을 따라간다 (winners run, losers cut).
    "trail_atr_mult": 0.0,
    "trail_activation_mult": 1.0,   # ATR × 이 값만큼 +가 나면 트레일 시작

    # 매매 관리
    "cooldown_bars": 3,
    "allow_long": True,
    "allow_short": True,
    "opposite_exit_enabled": True,  # EMA 반전 시 즉시 청산
}


STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "ema_fast": {"type": "integer", "min": 5, "max": 60, "label": "EMA Fast", "group": "Trend"},
    "ema_slow": {"type": "integer", "min": 20, "max": 300, "label": "EMA Slow", "group": "Trend"},
    "trend_strength_pct": {"type": "number", "min": 0.0, "max": 0.005, "label": "Trend gap %", "group": "Trend"},
    "rsi_period": {"type": "integer", "min": 3, "max": 30, "label": "RSI period", "group": "Entry"},
    "rsi_long_level": {"type": "number", "min": 20.0, "max": 55.0, "label": "RSI long X-level", "group": "Entry"},
    "rsi_short_level": {"type": "number", "min": 45.0, "max": 80.0, "label": "RSI short X-level", "group": "Entry"},
    "atr_period": {"type": "integer", "min": 5, "max": 60, "label": "ATR period", "group": "Vol"},
    "min_atr_pct": {"type": "number", "min": 0.0, "max": 0.01, "label": "Min ATR%", "group": "Vol"},
    "max_atr_pct": {"type": "number", "min": 0.001, "max": 0.05, "label": "Max ATR%", "group": "Vol"},
    "atr_tp_mult": {"type": "number", "min": 0.3, "max": 4.0, "label": "TP × ATR", "group": "Exit"},
    "atr_sl_mult": {"type": "number", "min": 0.2, "max": 3.0, "label": "SL × ATR", "group": "Exit"},
    "max_hold_bars": {"type": "integer", "min": 4, "max": 200, "label": "Max hold bars", "group": "Exit"},
    "cooldown_bars": {"type": "integer", "min": 0, "max": 30, "label": "Cooldown bars", "group": "Risk"},
    "allow_long": {"type": "boolean", "label": "Allow long", "group": "Risk"},
    "allow_short": {"type": "boolean", "label": "Allow short", "group": "Risk"},
    "opposite_exit_enabled": {"type": "boolean", "label": "Opposite-trend exit", "group": "Exit"},
}


# ---------------------------------------------------------------------------
# 전략 본체
# ---------------------------------------------------------------------------

class TrendPullbackScalperStrategy(Strategy):
    """Trend-Aligned Pullback Scalper (TAPS).

    - 1~5분봉용 양방향 스캘핑.
    - EMA 추세 + RSI 풀백 + ATR 스케일 TP/SL + time-stop + cooldown.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}

        self.ema_fast = int(p["ema_fast"])
        self.ema_slow = int(p["ema_slow"])
        self.trend_strength_pct = float(p["trend_strength_pct"])
        self.entry_mode = str(p["entry_mode"]).lower()
        self.donchian_period = int(p["donchian_period"])
        self.rsi_period = int(p["rsi_period"])
        self.rsi_long_level = float(p["rsi_long_level"])
        self.rsi_short_level = float(p["rsi_short_level"])
        self.atr_period = int(p["atr_period"])
        self.min_atr_pct = float(p["min_atr_pct"])
        self.max_atr_pct = float(p["max_atr_pct"])
        self.atr_tp_mult = float(p["atr_tp_mult"])
        self.atr_sl_mult = float(p["atr_sl_mult"])
        self.max_hold_bars = int(p["max_hold_bars"])
        self.trail_atr_mult = float(p["trail_atr_mult"])
        self.trail_activation_mult = float(p["trail_activation_mult"])
        self.cooldown_bars = int(p["cooldown_bars"])
        self.allow_long = bool(p["allow_long"])
        self.allow_short = bool(p["allow_short"])
        self.opposite_exit_enabled = bool(p["opposite_exit_enabled"])

        if self.ema_fast <= 1 or self.ema_slow <= 1:
            raise ValueError("EMA period must be > 1")
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be < ema_slow")
        if self.rsi_period <= 1:
            raise ValueError("rsi_period must be > 1")
        if self.atr_period <= 1:
            raise ValueError("atr_period must be > 1")
        if self.atr_tp_mult <= 0 or self.atr_sl_mult <= 0:
            raise ValueError("ATR multipliers must be > 0")
        if self.max_hold_bars <= 0:
            raise ValueError("max_hold_bars must be > 0")
        if self.entry_mode not in {"pullback", "breakout"}:
            raise ValueError("entry_mode must be 'pullback' or 'breakout'")
        if self.entry_mode == "breakout" and self.donchian_period < 2:
            raise ValueError("donchian_period must be >= 2 for breakout mode")
        if self.rsi_long_level >= self.rsi_short_level:
            # 일반적으로 long_level < short_level. 같거나 역전된 값은 사용자 실수.
            raise ValueError("rsi_long_level must be < rsi_short_level")
        if not (self.allow_long or self.allow_short):
            raise ValueError("At least one of allow_long/allow_short must be True")
        if self.trail_atr_mult < 0:
            raise ValueError("trail_atr_mult must be >= 0")

        # ----- runtime state -----
        self.prev_rsi: float | None = None
        self.prev_ema_fast: float | None = None
        self.prev_ema_slow: float | None = None
        self.is_closing: bool = False
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self.entry_price: float = 0.0
        self.entry_bar_index: int = -1
        self.bar_index: int = -1
        self._bars_since_close: int | None = None
        # trailing stop state
        self.hwm: float = 0.0   # high-water mark while long
        self.lwm: float = 0.0   # low-water mark while short
        self.trail_active: bool = False
        # donchian rolling window of (high, low) for last N bars
        self._dc_high: list[float] = []
        self._dc_low: list[float] = []

        # logging meta
        self.params = dict(p)
        self.indicator_config = {
            "EMA_FAST": {"period": self.ema_fast},
            "EMA_SLOW": {"period": self.ema_slow},
            "RSI": {"period": self.rsi_period},
            "ATR": {"period": self.atr_period},
        }

    # ---------------- lifecycle ----------------

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "EMA")
        register_talib_indicator_all_outputs(ctx, "RSI")
        register_talib_indicator_all_outputs(ctx, "ATR")

        self.prev_rsi = None
        self.prev_ema_fast = None
        self.prev_ema_slow = None
        self.is_closing = False
        self.tp_price = 0.0
        self.sl_price = 0.0
        self.entry_price = 0.0
        self.entry_bar_index = -1
        self.bar_index = -1
        self._bars_since_close = None
        self.hwm = 0.0
        self.lwm = 0.0
        self.trail_active = False
        self._dc_high.clear()
        self._dc_low.clear()

    # ---------------- helpers ----------------

    def _exit(self, ctx: StrategyContext, reason: str, exit_reason: str) -> None:
        self.is_closing = True
        self._bars_since_close = 0
        self.entry_bar_index = -1
        self.entry_price = 0.0
        self.tp_price = 0.0
        self.sl_price = 0.0
        self.hwm = 0.0
        self.lwm = 0.0
        self.trail_active = False
        ctx.close_position(reason=reason, exit_reason=exit_reason)

    # ---------------- on_bar ----------------

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # 청산 플래그 리셋
        if ctx.position_size == 0:
            self.is_closing = False

        # 라이브 미체결 가드
        if ctx.get_open_orders():
            return

        price = ctx.current_price

        # ===== 트레일링 스톱 업데이트 (틱 단위) =====
        if (
            ctx.position_size != 0
            and not self.is_closing
            and self.trail_atr_mult > 0
            and self.entry_price > 0
        ):
            # 최근 ATR을 다시 fetch
            try:
                _atr_now = float(ctx.get_indicator("ATR", period=self.atr_period))
            except Exception:  # noqa: BLE001
                _atr_now = float("nan")
            if math.isfinite(_atr_now) and _atr_now > 0:
                if ctx.position_size > 0:
                    if price > self.hwm:
                        self.hwm = price
                    activation = self.entry_price + _atr_now * self.trail_activation_mult
                    if self.hwm >= activation:
                        self.trail_active = True
                    if self.trail_active:
                        new_sl = self.hwm - _atr_now * self.trail_atr_mult
                        if new_sl > self.sl_price:
                            self.sl_price = new_sl
                else:  # short
                    if self.lwm == 0.0 or price < self.lwm:
                        self.lwm = price
                    activation = self.entry_price - _atr_now * self.trail_activation_mult
                    if self.lwm <= activation:
                        self.trail_active = True
                    if self.trail_active:
                        new_sl = self.lwm + _atr_now * self.trail_atr_mult
                        if self.sl_price == 0.0 or new_sl < self.sl_price:
                            self.sl_price = new_sl

        # ===== 청산 우선 (틱 단위로도 평가되도록 is_new_bar 무관) =====
        if ctx.position_size != 0 and not self.is_closing:
            if ctx.position_size > 0:
                if self.tp_price > 0 and price >= self.tp_price:
                    self._exit(ctx, f"TP Long {price:.4f}>={self.tp_price:.4f}", "TAKE_PROFIT")
                    return
                if self.sl_price > 0 and price <= self.sl_price:
                    self._exit(ctx, f"SL Long {price:.4f}<={self.sl_price:.4f}", "STOP_LOSS")
                    return
            else:
                if self.tp_price > 0 and price <= self.tp_price:
                    self._exit(ctx, f"TP Short {price:.4f}<={self.tp_price:.4f}", "TAKE_PROFIT")
                    return
                if self.sl_price > 0 and price >= self.sl_price:
                    self._exit(ctx, f"SL Short {price:.4f}>={self.sl_price:.4f}", "STOP_LOSS")
                    return

        # 새 봉 확정에서만 추세/신호 평가
        if not bool(bar.get("is_new_bar", True)):
            return

        # ----- per-new-bar bookkeeping -----
        self.bar_index += 1
        if self._bars_since_close is not None:
            self._bars_since_close += 1

        # 봉 high/low 를 Donchian 윈도우에 추가 (가장 최근 봉, 진입 신호용)
        try:
            bar_high = float(bar.get("high", price))
            bar_low = float(bar.get("low", price))
        except Exception:  # noqa: BLE001
            bar_high = price
            bar_low = price
        # 진입에는 "직전 N개 봉의 high/low" 가 필요하므로 현재 봉을 추가하기 전에 스냅샷.
        prev_dc_high = max(self._dc_high) if self._dc_high else 0.0
        prev_dc_low = min(self._dc_low) if self._dc_low else 0.0
        self._dc_high.append(bar_high)
        self._dc_low.append(bar_low)
        if len(self._dc_high) > self.donchian_period:
            self._dc_high.pop(0)
            self._dc_low.pop(0)

        # 인디케이터 조회
        ema_fast = float(ctx.get_indicator("EMA", period=self.ema_fast))
        ema_slow = float(ctx.get_indicator("EMA", period=self.ema_slow))
        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        atr = float(ctx.get_indicator("ATR", period=self.atr_period))

        # 모든 값이 유효한지 확인
        if not (math.isfinite(ema_fast) and math.isfinite(ema_slow)
                and math.isfinite(rsi) and math.isfinite(atr)):
            return

        # ===== Time-stop / Opposite-trend 청산 (새 봉에서만) =====
        if ctx.position_size != 0 and not self.is_closing:
            held = self.bar_index - self.entry_bar_index if self.entry_bar_index >= 0 else 0
            if held >= self.max_hold_bars:
                self._exit(ctx, f"Time-stop {held} bars", "TIME_STOP")
                return

            if self.opposite_exit_enabled and (self.prev_ema_fast is not None and self.prev_ema_slow is not None):
                # 롱 보유 중 EMA 데드 크로스 → 청산
                if ctx.position_size > 0:
                    crossed_dead = (self.prev_ema_fast >= self.prev_ema_slow) and (ema_fast < ema_slow)
                    if crossed_dead:
                        self._exit(ctx, "Opposite trend (EMA dead-cross)", "REVERSAL")
                        return
                # 숏 보유 중 EMA 골든 크로스 → 청산
                else:
                    crossed_golden = (self.prev_ema_fast <= self.prev_ema_slow) and (ema_fast > ema_slow)
                    if crossed_golden:
                        self._exit(ctx, "Opposite trend (EMA golden-cross)", "REVERSAL")
                        return

        # prev 상태 업데이트 (다음 봉용)
        prev_rsi = self.prev_rsi
        self.prev_rsi = rsi
        self.prev_ema_fast = ema_fast
        self.prev_ema_slow = ema_slow

        # ===== 진입 (포지션 없을 때만) =====
        if ctx.position_size != 0:
            return
        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            return

        # 변동성 게이트
        if price <= 0:
            return
        atr_pct = atr / price
        if not (self.min_atr_pct <= atr_pct <= self.max_atr_pct):
            return

        # 추세 마진
        trend_gap = (ema_fast - ema_slow) / ema_slow if ema_slow > 0 else 0.0
        is_uptrend = trend_gap > self.trend_strength_pct
        is_downtrend = trend_gap < -self.trend_strength_pct

        # ----- LONG -----
        if self.allow_long and is_uptrend:
            triggered = False
            entry_reason = ""
            if self.entry_mode == "pullback":
                if (
                    prev_rsi is not None
                    and math.isfinite(prev_rsi)
                    and _crossed_above(prev_rsi, rsi, self.rsi_long_level)
                    and price < ema_fast
                ):
                    triggered = True
                    entry_reason = (
                        f"Long pullback (rsi {prev_rsi:.1f}->{rsi:.1f}, "
                        f"trend_gap {trend_gap * 100:.2f}%, atr% {atr_pct * 100:.2f}%)"
                    )
            elif self.entry_mode == "breakout":
                # Donchian high breakout in uptrend
                if (
                    len(self._dc_high) >= self.donchian_period
                    and prev_dc_high > 0
                    and price > prev_dc_high
                ):
                    triggered = True
                    entry_reason = (
                        f"Long breakout (price {price:.2f} > {self.donchian_period}-bar high {prev_dc_high:.2f}, "
                        f"trend_gap {trend_gap * 100:.2f}%, atr% {atr_pct * 100:.2f}%)"
                    )

            if triggered:
                tp = price + atr * self.atr_tp_mult
                sl = price - atr * self.atr_sl_mult
                qty = float(ctx.calc_entry_quantity())
                if qty > 0:
                    self.tp_price = tp
                    self.sl_price = sl
                    self.entry_price = price
                    self.entry_bar_index = self.bar_index
                    self.hwm = price
                    self.lwm = 0.0
                    self.trail_active = False
                    ctx.buy(qty, price=None, reason=entry_reason)
                    return

        # ----- SHORT -----
        if self.allow_short and is_downtrend:
            triggered = False
            entry_reason = ""
            if self.entry_mode == "pullback":
                if (
                    prev_rsi is not None
                    and math.isfinite(prev_rsi)
                    and _crossed_below(prev_rsi, rsi, self.rsi_short_level)
                    and price > ema_fast
                ):
                    triggered = True
                    entry_reason = (
                        f"Short pullback (rsi {prev_rsi:.1f}->{rsi:.1f}, "
                        f"trend_gap {trend_gap * 100:.2f}%, atr% {atr_pct * 100:.2f}%)"
                    )
            elif self.entry_mode == "breakout":
                if (
                    len(self._dc_low) >= self.donchian_period
                    and prev_dc_low > 0
                    and price < prev_dc_low
                ):
                    triggered = True
                    entry_reason = (
                        f"Short breakout (price {price:.2f} < {self.donchian_period}-bar low {prev_dc_low:.2f}, "
                        f"trend_gap {trend_gap * 100:.2f}%, atr% {atr_pct * 100:.2f}%)"
                    )

            if triggered:
                tp = price - atr * self.atr_tp_mult
                sl = price + atr * self.atr_sl_mult
                qty = float(ctx.calc_entry_quantity())
                if qty > 0:
                    self.tp_price = tp
                    self.sl_price = sl
                    self.entry_price = price
                    self.entry_bar_index = self.bar_index
                    self.lwm = price
                    self.hwm = 0.0
                    self.trail_active = False
                    ctx.sell(qty, price=None, reason=entry_reason)
                    return
