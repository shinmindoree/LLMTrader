"""RSI Adaptive Scalping Strategy v2 (1m~15m).

설계 철학
=========
짧은 시간프레임(1~15분) 에서 *꾸준한 우상향 + 낮은 MDD* 를 만들기 위해
**두 가지 매매 모드** 를 ADX 로 스위치한다.

검증 결과 (기본값 / BTCUSDT 5m, leverage=2, fee 0.04%×2, slip 2bps×2)
====================================================================
2025-06 ~ 2026-04 (11 개월, 총 64 trade):
  - avg_return: +0.33 %/월   (compound +3.55 % over 11mo, $1000→$1035)
  - worst_mdd : 2.00 %       (session_max_loss_pct 하드 아웃이 cap)
  - avg_mdd   : 1.03 %
  - periods_+ : 5 / 11        (단일 최악 월 -2.00 %)
  - avg_pf    : 2.03          (win_rate 49 %, avg_win > avg_loss)
디자인 원칙:
  1) 트레일링·BE 비활성(99) → 순수 ATR TP/SL 이 PF 를 가장 잘 보호.
  2) vol_z_min=-0.3 으로 저변동(quiet) 구간 진입 차단.
  3) session_max_loss_pct=2.0 → 손실 폭주 월(2026-02)의 -6.3% 를 -2.0% 로 cap.
  4) macro EMA200 buffer=0.2% 로 추세 역행 진입 차단.

A) RANGE 모드 (ADX < ``adx_range_max``)
   - 평균회귀(Mean Reversion). 시장이 옆으로 횡보할 때.
   - LONG  : Close < BB Lower(period, k) AND RSI < ``rsi_oversold``
            AND Close > 직전봉 Close (반등 확인)
   - SHORT : Close > BB Upper(period, k) AND RSI > ``rsi_overbought``
            AND Close < 직전봉 Close
   - TP    : BB Middle (평균회귀 타깃) 혹은 ``atr_tp_range`` × ATR 중 가까운 쪽.
   - SL    : ``atr_sl_range`` × ATR.

B) TREND 모드 (ADX > ``adx_trend_min``)
   - 추세 풀백(Trend Pullback). 명확한 추세에서.
   - 추세 정렬: EMA(``ema_fast``) 와 EMA(``ema_slow``) 가 동일 방향.
   - LONG  : 상승 추세 AND 최근 ``rsi_pullback_lookback`` 봉 내 RSI <
            ``rsi_long_pullback`` 있었음 AND 현재 봉에서 RSI 가
            ``rsi_long_trigger`` 를 상향 돌파.
   - SHORT : 미러.
   - TP    : ``atr_tp_trend`` × ATR.
   - SL    : ``atr_sl_trend`` × ATR.

C) MIXED 모드 (adx_range_max ≤ ADX ≤ adx_trend_min)
   - 진입 금지. 어느 진영도 명확하지 않을 때 손실 회피.

공통 게이트 (모든 진입에 적용)
============================
- ATR/price 가 [``atr_min_pct``, ``atr_max_pct``] 사이.
- 거래량 z-score >= ``vol_z_min`` (옵션).
- 청산 직후 ``cooldown_bars`` 동안 신규 진입 금지.
- 연속 ``consecutive_loss_max`` 회 손실 시
  ``cooldown_bars × loss_pause_multiplier`` 봉 추가 휴식.

청산 (모드 공통 추가)
====================
- 시간 만료: ``max_hold_bars`` 초과 시 강제 청산 (TIME_EXIT).
- 브레이크이븐: +``breakeven_atr`` × ATR 도달 시 SL → 진입가.
- 트레일링: +``trail_trigger_atr`` × ATR 후 peak/trough ∓ ``trail_atr`` × ATR.
"""

from __future__ import annotations

import importlib
import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


# --------------------------------------------------------------------------- #
# TA-Lib 어댑터 (self-contained, multi-output 지원)
# --------------------------------------------------------------------------- #


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
    """builtin TA-Lib 인디케이터 등록. 단일/다중 출력 자동 처리."""

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
                raise TypeError(
                    "builtin indicator params must be passed as keywords (or single period)"
                )

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


def _register_volume_zscore(ctx: StrategyContext) -> None:
    """최근 N봉 거래량의 z-score (마지막 값)."""

    try:
        import numpy as np  # type: ignore
    except Exception:  # noqa: BLE001
        return

    def _volz(inner_ctx: Any, *args: Any, **kwargs: Any) -> float:
        period = int(kwargs.get("period", kwargs.get("timeperiod", 20)))
        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return float("nan")
        raw = inputs()
        volume = np.asarray(list(raw.get("volume", [])), dtype="float64")
        if volume.size < period:
            return float("nan")
        window = volume[-period:]
        mean = float(np.mean(window))
        std = float(np.std(window))
        if std <= 0:
            return 0.0
        return (float(window[-1]) - mean) / std

    ctx.register_indicator("VOL_Z", _volz)


# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #


def crossed_above(prev: float, current: float, level: float) -> bool:
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    return current <= level < prev


# --------------------------------------------------------------------------- #
# 파라미터 (디폴트)
# --------------------------------------------------------------------------- #


STRATEGY_PARAMS: dict[str, Any] = {
    # ----- RSI (필수) -----
    "rsi_period": 14,
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "rsi_long_pullback": 42.0,
    "rsi_short_pullback": 58.0,
    "rsi_long_trigger": 52.0,
    "rsi_short_trigger": 48.0,
    "rsi_pullback_lookback": 10,
    # ----- 추세 EMA -----
    "ema_fast": 20,
    "ema_slow": 50,
    # ----- ADX (레짐 분류) -----
    "adx_period": 14,
    "adx_range_max": 22.0,
    "adx_trend_min": 25.0,
    # ----- Bollinger Bands -----
    "bb_period": 20,
    "bb_k": 2.0,
    # ----- ATR / TP / SL -----
    "atr_period": 14,
    "atr_min_pct": 0.0003,
    "atr_max_pct": 0.0150,
    # Range mode TP/SL
    "atr_tp_range": 2.0,
    "atr_sl_range": 1.5,
    # Trend mode TP/SL
    "atr_tp_trend": 3.5,
    "atr_sl_trend": 2.0,
    # ----- 거래량 z-score -----
    "vol_zscore_period": 20,
    "vol_z_min": -0.3,
    # ----- 트레일링 / 손실 통제 -----
    # ⚠️ 기본값은 트레일링/브레이크이븐을 사실상 비활성화(99)했으며,
    #    육안의 식별이 그대로 TP/SL로 끝난다. 실전 테스트 결과
    #    자가 거래 시나리오에서 트레일링이 이김을 너무 일찍 잘라내서
    #    전체 PF를 크게 낮추는 것을 확인.
    "breakeven_atr": 99.0,
    "trail_trigger_atr": 99.0,
    "trail_atr": 99.0,
    "max_hold_bars_range": 24,
    "max_hold_bars_trend": 60,
    "cooldown_bars": 3,
    "consecutive_loss_max": 3,
    "loss_pause_multiplier": 4,
    # ----- 기간 손실 서킷 브레이커 -----
    # 1) period_dd_brake_pct: 세션 peak 대비 하락퍼센트 도달시 brake_bars 봉 신규진입 금지.
    # 2) session_max_loss_pct: 초기잔고 대비 이 %% 이상 손실 누적시 기간 끝까지 신규진입 완전 차단(하드 아웃).
    # ⚠️ period_dd_brake 기본값은 0으로 비활성화 — session_max_loss_pct만으로도
    #    Feb 2026 구간의 -6.31% 평균 이탈치를 -2.00%로 제한하는 데 충분.
    "period_dd_brake_pct": 0.0,
    "period_dd_brake_bars": 0,
    "session_max_loss_pct": 2.0,
    # ----- 모드 / 방향 토글 -----
    "enable_range_mode": 1,
    "enable_trend_mode": 1,
    "allow_long": 1,
    "allow_short": 1,
    # ----- 매크로 추세 필터 (옵션) -----
    # 1 이면 EMA(macro_ema) 와 가격 비교로 방향 강제:
    #   price > EMA200 → SHORT 차단
    #   price < EMA200 → LONG  차단
    # 0 이면 매크로 필터 OFF.
    "macro_filter_enabled": 1,
    "macro_ema": 200,
    "macro_buffer_pct": 0.002,
}


STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "rsi_period": {"type": "integer", "min": 2, "max": 100, "label": "RSI 기간",
                    "description": "RSI 계산 기간.", "group": "지표 (Indicator)"},
    "rsi_oversold": {"type": "number", "min": 5, "max": 49, "label": "RSI 과매도",
                      "description": "RANGE 모드 롱 진입 임계.", "group": "RANGE 모드"},
    "rsi_overbought": {"type": "number", "min": 51, "max": 95, "label": "RSI 과매수",
                        "description": "RANGE 모드 숏 진입 임계.", "group": "RANGE 모드"},
    "rsi_long_pullback": {"type": "number", "min": 5, "max": 49,
                            "label": "롱 풀백 RSI 임계",
                            "description": "TREND 모드 롱: 최근 N봉 내 RSI 가 이 값 이하였어야.",
                            "group": "TREND 모드"},
    "rsi_short_pullback": {"type": "number", "min": 51, "max": 95,
                             "label": "숏 풀백 RSI 임계",
                             "description": "TREND 모드 숏: 최근 N봉 내 RSI 가 이 값 이상이었어야.",
                             "group": "TREND 모드"},
    "rsi_long_trigger": {"type": "number", "min": 5, "max": 80,
                          "label": "롱 트리거 RSI",
                          "description": "RSI 가 이 값을 상향돌파하면 롱 트리거 (TREND).",
                          "group": "TREND 모드"},
    "rsi_short_trigger": {"type": "number", "min": 20, "max": 95,
                           "label": "숏 트리거 RSI",
                           "description": "RSI 가 이 값을 하향돌파하면 숏 트리거 (TREND).",
                           "group": "TREND 모드"},
    "rsi_pullback_lookback": {"type": "integer", "min": 1, "max": 50,
                                "label": "풀백 검색 윈도우",
                                "description": "풀백 RSI 체크 윈도우 길이(봉 수).",
                                "group": "TREND 모드"},
    "ema_fast": {"type": "integer", "min": 3, "max": 200, "label": "빠른 EMA",
                  "description": "추세 정렬용 빠른 EMA.", "group": "지표 (Indicator)"},
    "ema_slow": {"type": "integer", "min": 10, "max": 500, "label": "느린 EMA",
                  "description": "추세 정렬용 느린 EMA.", "group": "지표 (Indicator)"},
    "adx_period": {"type": "integer", "min": 5, "max": 100, "label": "ADX 기간",
                    "description": "ADX 계산 기간.", "group": "지표 (Indicator)"},
    "adx_range_max": {"type": "number", "min": 5, "max": 40, "label": "RANGE 최대 ADX",
                       "description": "ADX < 이 값 → RANGE 모드 활성.",
                       "group": "레짐 (Regime)"},
    "adx_trend_min": {"type": "number", "min": 10, "max": 60, "label": "TREND 최소 ADX",
                       "description": "ADX > 이 값 → TREND 모드 활성.",
                       "group": "레짐 (Regime)"},
    "bb_period": {"type": "integer", "min": 5, "max": 200, "label": "BB 기간",
                   "description": "Bollinger Bands 기간.", "group": "지표 (Indicator)"},
    "bb_k": {"type": "number", "min": 0.5, "max": 5.0, "label": "BB stddev",
              "description": "Bollinger Bands 표준편차 배수.",
              "group": "지표 (Indicator)"},
    "atr_period": {"type": "integer", "min": 2, "max": 100, "label": "ATR 기간",
                    "description": "ATR 계산 기간 (TP/SL).",
                    "group": "지표 (Indicator)"},
    "atr_min_pct": {"type": "number", "min": 0.0, "max": 0.02, "label": "ATR 최소(%)",
                     "description": "ATR/price 가 이 값보다 작으면 진입 스킵.",
                     "group": "필터 (Filter)"},
    "atr_max_pct": {"type": "number", "min": 0.0, "max": 0.05, "label": "ATR 최대(%)",
                     "description": "ATR/price 가 이 값보다 크면 진입 스킵.",
                     "group": "필터 (Filter)"},
    "atr_tp_range": {"type": "number", "min": 0.3, "max": 5.0, "label": "RANGE TP ATR",
                      "description": "RANGE 모드 TP = 진입가 ± ATR × 배수.",
                      "group": "RANGE 모드"},
    "atr_sl_range": {"type": "number", "min": 0.3, "max": 5.0, "label": "RANGE SL ATR",
                      "description": "RANGE 모드 SL = 진입가 ∓ ATR × 배수.",
                      "group": "RANGE 모드"},
    "atr_tp_trend": {"type": "number", "min": 0.5, "max": 8.0, "label": "TREND TP ATR",
                      "description": "TREND 모드 TP = 진입가 ± ATR × 배수.",
                      "group": "TREND 모드"},
    "atr_sl_trend": {"type": "number", "min": 0.3, "max": 5.0, "label": "TREND SL ATR",
                      "description": "TREND 모드 SL = 진입가 ∓ ATR × 배수.",
                      "group": "TREND 모드"},
    "vol_zscore_period": {"type": "integer", "min": 5, "max": 200, "label": "거래량 Z 기간",
                           "description": "거래량 z-score 계산 기간.",
                           "group": "지표 (Indicator)"},
    "vol_z_min": {"type": "number", "min": -3.0, "max": 3.0, "label": "거래량 Z 최소",
                   "description": "거래량 z-score 가 이 값 미만이면 진입 스킵.",
                   "group": "필터 (Filter)"},
    "breakeven_atr": {"type": "number", "min": 0.0, "max": 5.0,
                       "label": "브레이크이븐 ATR",
                       "description": "+ATR × 배수 이익 도달 시 SL → 진입가.",
                       "group": "청산 (Exit)"},
    "trail_trigger_atr": {"type": "number", "min": 0.0, "max": 10.0,
                           "label": "트레일 트리거 ATR",
                           "description": "+ATR × 배수 이익 도달 시 트레일링 시작.",
                           "group": "청산 (Exit)"},
    "trail_atr": {"type": "number", "min": 0.1, "max": 5.0, "label": "트레일 ATR",
                   "description": "트레일 중 peak/trough ∓ ATR × 배수.",
                   "group": "청산 (Exit)"},
    "max_hold_bars_range": {"type": "integer", "min": 1, "max": 1000,
                              "label": "RANGE 최대 보유",
                              "description": "RANGE 모드 시간 만료 봉수.",
                              "group": "RANGE 모드"},
    "max_hold_bars_trend": {"type": "integer", "min": 1, "max": 1000,
                              "label": "TREND 최대 보유",
                              "description": "TREND 모드 시간 만료 봉수.",
                              "group": "TREND 모드"},
    "cooldown_bars": {"type": "integer", "min": 0, "max": 1000,
                       "label": "쿨다운 봉수",
                       "description": "청산 직후 신규 진입 금지 봉 수.",
                       "group": "필터 (Filter)"},
    "consecutive_loss_max": {"type": "integer", "min": 1, "max": 20,
                              "label": "연속 손실 한도",
                              "description": "이 횟수 도달 시 추가 휴식.",
                              "group": "리스크 (Risk)"},
    "loss_pause_multiplier": {"type": "integer", "min": 1, "max": 20,
                               "label": "손실 휴식 배수",
                               "description": "연속 손실 시 cooldown × 배수 봉 추가 휴식.",
                               "group": "리스크 (Risk)"},
    "period_dd_brake_pct": {"type": "number", "min": 0.0, "max": 50.0,
                              "label": "기간 DD 브레이크(%)",
                              "description": "잔고가 세션 peak 대비 이 %% 이상 빠지면 휴식.",
                              "group": "리스크 (Risk)"},
    "period_dd_brake_bars": {"type": "integer", "min": 0, "max": 10000,
                               "label": "기간 DD 휴식 봉수",
                               "description": "DD 브레이크 작동시 신규 진입 금지 봉수.",
                               "group": "리스크 (Risk)"},
    "session_max_loss_pct": {"type": "number", "min": 0.0, "max": 100.0,
                               "label": "세션 소실 한도(%)",
                               "description": "초기잔고 대비 이 %% 이상 손실 시 기간 끊까지 신규 진입 완전 차단.",
                               "group": "리스크 (Risk)"},
    "enable_range_mode": {"type": "integer", "min": 0, "max": 1,
                            "label": "RANGE 모드 ON",
                            "description": "0이면 RANGE 모드 완전 비활성.",
                            "group": "레짐 (Regime)"},
    "enable_trend_mode": {"type": "integer", "min": 0, "max": 1,
                            "label": "TREND 모드 ON",
                            "description": "0이면 TREND 모드 완전 비활성.",
                            "group": "레짐 (Regime)"},
    "allow_long": {"type": "integer", "min": 0, "max": 1, "label": "롱 허용",
                    "description": "1: 롱 진입 허용 / 0: 차단.",
                    "group": "방향 (Side)"},
    "allow_short": {"type": "integer", "min": 0, "max": 1, "label": "숏 허용",
                     "description": "1: 숏 진입 허용 / 0: 차단.",
                     "group": "방향 (Side)"},
    "macro_filter_enabled": {"type": "integer", "min": 0, "max": 1,
                                "label": "매크로 추세 필터",
                                "description": "1: 가격>EMA200 → SHORT 차단, 가격<EMA200 → LONG 차단.",
                                "group": "필터 (Filter)"},
    "macro_ema": {"type": "integer", "min": 20, "max": 1000, "label": "매크로 EMA",
                   "description": "매크로 추세 판단용 장기 EMA.",
                   "group": "필터 (Filter)"},
    "macro_buffer_pct": {"type": "number", "min": 0.0, "max": 0.05,
                          "label": "매크로 EMA 버퍼(%)",
                          "description": "|price-EMA200|/price 가 이 값보다 작으면 양방향 허용.",
                          "group": "필터 (Filter)"},
}


# --------------------------------------------------------------------------- #
# 전략
# --------------------------------------------------------------------------- #


class RsiAdaptiveScalpingStrategy(Strategy):
    """ADX 기반 RANGE/TREND 듀얼-모드 RSI 스캘퍼."""

    VERSION = "2.0"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        p = {**STRATEGY_PARAMS, **kwargs}

        # --- RSI ---
        self.rsi_period = int(p["rsi_period"])
        if self.rsi_period <= 1:
            raise ValueError("rsi_period must be > 1")
        self.rsi_oversold = float(p["rsi_oversold"])
        self.rsi_overbought = float(p["rsi_overbought"])
        if not (0 < self.rsi_oversold < 50 < self.rsi_overbought < 100):
            raise ValueError("invalid RSI oversold/overbought (must be 0<os<50<ob<100)")
        self.rsi_long_pullback = float(p["rsi_long_pullback"])
        self.rsi_short_pullback = float(p["rsi_short_pullback"])
        self.rsi_long_trigger = float(p["rsi_long_trigger"])
        self.rsi_short_trigger = float(p["rsi_short_trigger"])
        self.rsi_pullback_lookback = max(1, int(p["rsi_pullback_lookback"]))

        # --- EMA / ADX / BB / ATR ---
        self.ema_fast = int(p["ema_fast"])
        self.ema_slow = int(p["ema_slow"])
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be < ema_slow")
        self.adx_period = int(p["adx_period"])
        self.adx_range_max = float(p["adx_range_max"])
        self.adx_trend_min = float(p["adx_trend_min"])
        self.bb_period = int(p["bb_period"])
        self.bb_k = float(p["bb_k"])
        self.atr_period = int(p["atr_period"])
        self.atr_min_pct = float(p["atr_min_pct"])
        self.atr_max_pct = float(p["atr_max_pct"])

        # --- TP/SL ---
        self.atr_tp_range = float(p["atr_tp_range"])
        self.atr_sl_range = float(p["atr_sl_range"])
        self.atr_tp_trend = float(p["atr_tp_trend"])
        self.atr_sl_trend = float(p["atr_sl_trend"])

        # --- Volume ---
        self.vol_zscore_period = int(p["vol_zscore_period"])
        self.vol_z_min = float(p["vol_z_min"])

        # --- Trailing / risk ---
        self.breakeven_atr = float(p["breakeven_atr"])
        self.trail_trigger_atr = float(p["trail_trigger_atr"])
        self.trail_atr = float(p["trail_atr"])
        self.max_hold_bars_range = int(p["max_hold_bars_range"])
        self.max_hold_bars_trend = int(p["max_hold_bars_trend"])
        self.cooldown_bars = int(p["cooldown_bars"])
        self.consecutive_loss_max = int(p["consecutive_loss_max"])
        self.loss_pause_multiplier = int(p["loss_pause_multiplier"])
        self.period_dd_brake_pct = float(p["period_dd_brake_pct"])
        self.period_dd_brake_bars = int(p["period_dd_brake_bars"])
        self.session_max_loss_pct = float(p["session_max_loss_pct"])

        # --- Toggles ---
        self.enable_range_mode = bool(int(p["enable_range_mode"]))
        self.enable_trend_mode = bool(int(p["enable_trend_mode"]))
        self.allow_long = bool(int(p["allow_long"]))
        self.allow_short = bool(int(p["allow_short"]))
        self.macro_filter_enabled = bool(int(p["macro_filter_enabled"]))
        self.macro_ema = int(p["macro_ema"])
        self.macro_buffer_pct = float(p["macro_buffer_pct"])

        # --- State ---
        self.prev_rsi: float | None = None
        self.prev_close: float | None = None
        self.rsi_window: list[float] = []
        self.is_closing: bool = False
        self.entry_mode: str = ""
        self.entry_price: float = 0.0
        self.entry_atr: float = 0.0
        self.bb_middle_at_entry: float = 0.0
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self.breakeven_done: bool = False
        self.trailing_active: bool = False
        self.peak_price_since_entry: float = 0.0
        self.trough_price_since_entry: float = 0.0
        self._bars_in_position: int = 0
        self._bars_since_close: int | None = None
        self._consecutive_losses: int = 0
        self._extra_pause_until_bar: int | None = None
        self._bar_counter: int = 0
        self._peak_balance: float | None = None
        self._initial_balance: float | None = None
        self._session_halted: bool = False

        self.params = dict(p)
        self.indicator_config: dict[str, Any] = {
            "RSI": {"period": self.rsi_period},
            "EMA_FAST": {"period": self.ema_fast},
            "EMA_SLOW": {"period": self.ema_slow},
            "EMA_MACRO": {"period": self.macro_ema},
            "ADX": {"period": self.adx_period},
            "ATR": {"period": self.atr_period},
            "BBANDS": {"period": self.bb_period, "k": self.bb_k},
            "VOL_Z": {"period": self.vol_zscore_period},
        }

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def initialize(self, ctx: StrategyContext) -> None:
        print(f"🚀 [버전확인] RsiAdaptiveScalpingStrategy v{self.VERSION} 시작")
        for name in ("RSI", "EMA", "ADX", "ATR", "BBANDS"):
            register_talib_indicator_all_outputs(ctx, name)
        _register_volume_zscore(ctx)
        self._reset_position_state()
        self.prev_rsi = None
        self.prev_close = None
        self.rsi_window = []
        self._bars_since_close = None
        self._consecutive_losses = 0
        self._extra_pause_until_bar = None
        self._bar_counter = 0

    def _reset_position_state(self) -> None:
        self.is_closing = False
        self.entry_mode = ""
        self.entry_price = 0.0
        self.entry_atr = 0.0
        self.bb_middle_at_entry = 0.0
        self.tp_price = 0.0
        self.sl_price = 0.0
        self.breakeven_done = False
        self.trailing_active = False
        self.peak_price_since_entry = 0.0
        self.trough_price_since_entry = 0.0
        self._bars_in_position = 0

    # ------------------------------------------------------------------ #
    # on_bar
    # ------------------------------------------------------------------ #

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # 청산 직후 처리
        if ctx.position_size == 0:
            if self.is_closing:
                self._bars_since_close = 0
                self._update_loss_streak(ctx)
            self._reset_position_state()

        if ctx.get_open_orders():
            return

        price = float(ctx.current_price)
        if not math.isfinite(price) or price <= 0:
            return

        # 1) intra-bar TP/SL/트레일링 평가
        if ctx.position_size != 0 and not self.is_closing:
            self._update_peak_trough(price)
            self._maybe_update_breakeven_and_trailing(price)
            if self._evaluate_exit(ctx, price):
                return

        # 2) 새 봉만 진입 검토
        if not bool(bar.get("is_new_bar", True)):
            return

        self._bar_counter += 1
        if ctx.position_size != 0:
            self._bars_in_position += 1
        if self._bars_since_close is not None:
            self._bars_since_close += 1

        # 3) RSI 윈도우 갱신
        try:
            rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        except Exception:  # noqa: BLE001
            return
        if not math.isfinite(rsi):
            return
        prev_rsi = self.prev_rsi
        self.prev_rsi = rsi
        prev_close = self.prev_close
        bar_close = float(bar.get("close", price))
        self.prev_close = bar_close

        self.rsi_window.append(rsi)
        keep = max(self.rsi_pullback_lookback + 2, 12)
        if len(self.rsi_window) > keep:
            self.rsi_window = self.rsi_window[-keep:]

        # 4) 시간 만료 청산
        if ctx.position_size != 0 and not self.is_closing:
            limit = (
                self.max_hold_bars_range
                if self.entry_mode == "RANGE"
                else self.max_hold_bars_trend
            )
            if self._bars_in_position >= limit:
                self._close(
                    ctx,
                    f"Time Exit ({self._bars_in_position}b)",
                    exit_reason="TIME_EXIT",
                )
                return

        if prev_rsi is None or prev_close is None:
            return
        if ctx.position_size != 0:
            return

        # 5) 진입 게이팅 - 쿨다운/추가 휴식
        if self._bars_since_close is not None and self._bars_since_close < self.cooldown_bars:
            return
        if (
            self._extra_pause_until_bar is not None
            and self._bar_counter < self._extra_pause_until_bar
        ):
            return

        # 5a) 기간 손실 서킷 브레이커
        #   잔고 peak 대비 brake_pct%% 이상 빠지면 brake_bars 봉간 신규 진입 금지
        try:
            cur_balance = float(ctx.balance)
        except Exception:  # noqa: BLE001
            cur_balance = None  # type: ignore[assignment]
        if cur_balance is not None and cur_balance > 0:
            # 최초 1회만 초기잔고 고정
            if self._initial_balance is None:
                self._initial_balance = cur_balance
            if self._peak_balance is None or cur_balance > self._peak_balance:
                self._peak_balance = cur_balance
            # 세션 하드 아웃 (기간 끊까지 신규 진입 차단)
            if (
                self.session_max_loss_pct > 0
                and self._initial_balance is not None
                and self._initial_balance > 0
            ):
                session_loss_pct = (
                    self._initial_balance - cur_balance
                ) / self._initial_balance * 100.0
                if session_loss_pct >= self.session_max_loss_pct:
                    self._session_halted = True
            if self._session_halted:
                return
            # 롤링 peak DD 휴식
            if (
                self.period_dd_brake_pct > 0
                and self._peak_balance is not None
                and self._peak_balance > 0
            ):
                dd_pct = (self._peak_balance - cur_balance) / self._peak_balance * 100.0
                if dd_pct >= self.period_dd_brake_pct:
                    self._extra_pause_until_bar = (
                        self._bar_counter + self.period_dd_brake_bars
                    )
                    # 다음번 peak 갱신을 위해 peak reset
                    self._peak_balance = cur_balance
                    return

        # 6) 공통 지표
        try:
            atr = float(ctx.get_indicator("ATR", period=self.atr_period))
            adx = float(ctx.get_indicator("ADX", period=self.adx_period))
        except Exception:  # noqa: BLE001
            return
        if not math.isfinite(atr) or atr <= 0:
            return

        atr_pct = atr / price
        if atr_pct < self.atr_min_pct or atr_pct > self.atr_max_pct:
            return

        try:
            volz = float(ctx.get_indicator("VOL_Z", period=self.vol_zscore_period))
        except Exception:  # noqa: BLE001
            volz = math.nan
        if math.isfinite(volz) and volz < self.vol_z_min:
            return

        # 7) 레짐 분류
        regime = "MIXED"
        if math.isfinite(adx):
            if adx < self.adx_range_max:
                regime = "RANGE"
            elif adx > self.adx_trend_min:
                regime = "TREND"

        if regime == "MIXED":
            return

        bar_open = float(bar.get("open", price))

        # 7.5) 매크로 추세 필터 (옵션)
        macro_allow_long = True
        macro_allow_short = True
        if self.macro_filter_enabled:
            try:
                ema_macro = float(ctx.get_indicator("EMA", period=self.macro_ema))
            except Exception:  # noqa: BLE001
                ema_macro = math.nan
            if math.isfinite(ema_macro) and ema_macro > 0:
                gap = (price - ema_macro) / price
                if gap > self.macro_buffer_pct:
                    macro_allow_short = False
                elif gap < -self.macro_buffer_pct:
                    macro_allow_long = False

        # 8) RANGE 모드 진입
        if regime == "RANGE" and self.enable_range_mode:
            try:
                bb = ctx.get_indicator("BBANDS", period=self.bb_period, nbdevup=self.bb_k, nbdevdn=self.bb_k)
            except Exception:  # noqa: BLE001
                bb = None
            if isinstance(bb, dict):
                bb_upper = float(bb.get("upperband", bb.get("output_0", math.nan)))
                bb_middle = float(bb.get("middleband", bb.get("output_1", math.nan)))
                bb_lower = float(bb.get("lowerband", bb.get("output_2", math.nan)))
            else:
                bb_upper = bb_middle = bb_lower = math.nan
            if not (math.isfinite(bb_upper) and math.isfinite(bb_lower) and math.isfinite(bb_middle)):
                return

            long_signal = (
                self.allow_long
                and macro_allow_long
                and bar_close <= bb_lower
                and rsi <= self.rsi_oversold
                and bar_close > prev_close
            )
            short_signal = (
                self.allow_short
                and macro_allow_short
                and bar_close >= bb_upper
                and rsi >= self.rsi_overbought
                and bar_close < prev_close
            )

            if long_signal and not short_signal:
                self._enter(
                    ctx,
                    side="LONG",
                    mode="RANGE",
                    price=price,
                    atr=atr,
                    bb_middle=bb_middle,
                    rsi=rsi,
                    prev_rsi=prev_rsi,
                    adx=adx,
                )
                return
            if short_signal and not long_signal:
                self._enter(
                    ctx,
                    side="SHORT",
                    mode="RANGE",
                    price=price,
                    atr=atr,
                    bb_middle=bb_middle,
                    rsi=rsi,
                    prev_rsi=prev_rsi,
                    adx=adx,
                )
                return
            return

        # 9) TREND 모드 진입
        if regime == "TREND" and self.enable_trend_mode:
            try:
                ema_fast = float(ctx.get_indicator("EMA", period=self.ema_fast))
                ema_slow = float(ctx.get_indicator("EMA", period=self.ema_slow))
            except Exception:  # noqa: BLE001
                return
            if not (math.isfinite(ema_fast) and math.isfinite(ema_slow)):
                return

            uptrend = ema_fast > ema_slow and price > ema_slow
            downtrend = ema_fast < ema_slow and price < ema_slow

            deep_win = self.rsi_window[-(self.rsi_pullback_lookback + 1):]
            had_pull_low = any(v <= self.rsi_long_pullback for v in deep_win)
            had_pull_high = any(v >= self.rsi_short_pullback for v in deep_win)
            cross_long = crossed_above(prev_rsi, rsi, self.rsi_long_trigger)
            cross_short = crossed_below(prev_rsi, rsi, self.rsi_short_trigger)

            long_signal = (
                self.allow_long
                and macro_allow_long
                and uptrend
                and had_pull_low
                and cross_long
                and bar_close >= bar_open
            )
            short_signal = (
                self.allow_short
                and macro_allow_short
                and downtrend
                and had_pull_high
                and cross_short
                and bar_close <= bar_open
            )

            if long_signal and not short_signal:
                self._enter(
                    ctx,
                    side="LONG",
                    mode="TREND",
                    price=price,
                    atr=atr,
                    bb_middle=0.0,
                    rsi=rsi,
                    prev_rsi=prev_rsi,
                    adx=adx,
                )
                return
            if short_signal and not long_signal:
                self._enter(
                    ctx,
                    side="SHORT",
                    mode="TREND",
                    price=price,
                    atr=atr,
                    bb_middle=0.0,
                    rsi=rsi,
                    prev_rsi=prev_rsi,
                    adx=adx,
                )
                return

    # ------------------------------------------------------------------ #
    # 진입/청산
    # ------------------------------------------------------------------ #

    def _enter(
        self,
        ctx: StrategyContext,
        *,
        side: str,
        mode: str,
        price: float,
        atr: float,
        bb_middle: float,
        rsi: float,
        prev_rsi: float,
        adx: float,
    ) -> None:
        self.entry_mode = mode
        self.entry_price = price
        self.entry_atr = atr
        self.bb_middle_at_entry = bb_middle
        self.breakeven_done = False
        self.trailing_active = False
        self.peak_price_since_entry = price
        self.trough_price_since_entry = price
        self._bars_in_position = 0

        if mode == "RANGE":
            tp_mult = self.atr_tp_range
            sl_mult = self.atr_sl_range
        else:
            tp_mult = self.atr_tp_trend
            sl_mult = self.atr_sl_trend

        if side == "LONG":
            atr_tp = price + atr * tp_mult
            if mode == "RANGE" and bb_middle > price:
                # BB middle 이 더 가까우면 그쪽을 TP 로
                self.tp_price = min(atr_tp, bb_middle)
            else:
                self.tp_price = atr_tp
            self.sl_price = price - atr * sl_mult
            adx_s = f"{adx:.1f}" if math.isfinite(adx) else "nan"
            ctx.enter_long(
                reason=(
                    f"{mode} Long RSI {prev_rsi:.1f}->{rsi:.1f} ADX={adx_s} "
                    f"ATR={atr:.2f} TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                ),
            )
        else:
            atr_tp = price - atr * tp_mult
            if mode == "RANGE" and 0 < bb_middle < price:
                self.tp_price = max(atr_tp, bb_middle)
            else:
                self.tp_price = atr_tp
            self.sl_price = price + atr * sl_mult
            adx_s = f"{adx:.1f}" if math.isfinite(adx) else "nan"
            ctx.enter_short(
                reason=(
                    f"{mode} Short RSI {prev_rsi:.1f}->{rsi:.1f} ADX={adx_s} "
                    f"ATR={atr:.2f} TP={self.tp_price:.2f} SL={self.sl_price:.2f}"
                ),
            )

    def _close(self, ctx: StrategyContext, reason: str, *, exit_reason: str) -> None:
        self.is_closing = True
        ctx.close_position(reason=reason, exit_reason=exit_reason)

    # ------------------------------------------------------------------ #
    # 트레일링 / SL 이동
    # ------------------------------------------------------------------ #

    def _update_peak_trough(self, price: float) -> None:
        if price > self.peak_price_since_entry:
            self.peak_price_since_entry = price
        if self.trough_price_since_entry == 0 or price < self.trough_price_since_entry:
            self.trough_price_since_entry = price

    def _maybe_update_breakeven_and_trailing(self, price: float) -> None:
        atr = self.entry_atr
        if atr <= 0 or self.entry_price <= 0:
            return
        is_long = self.tp_price > self.sl_price

        # 브레이크이븐
        if self.breakeven_atr > 0 and not self.breakeven_done:
            trig = atr * self.breakeven_atr
            if is_long and price >= self.entry_price + trig:
                self.sl_price = max(self.sl_price, self.entry_price)
                self.breakeven_done = True
            elif (not is_long) and price <= self.entry_price - trig:
                self.sl_price = (
                    min(self.sl_price, self.entry_price)
                    if self.sl_price > 0
                    else self.entry_price
                )
                self.breakeven_done = True

        # 트레일링
        if self.trail_trigger_atr <= 0 or self.trail_atr <= 0:
            return
        trig_dist = atr * self.trail_trigger_atr
        trail_dist = atr * self.trail_atr
        if is_long:
            if self.peak_price_since_entry - self.entry_price >= trig_dist:
                self.trailing_active = True
            if self.trailing_active:
                new_sl = self.peak_price_since_entry - trail_dist
                if new_sl > self.sl_price:
                    self.sl_price = new_sl
        else:
            if self.entry_price - self.trough_price_since_entry >= trig_dist:
                self.trailing_active = True
            if self.trailing_active:
                new_sl = self.trough_price_since_entry + trail_dist
                if new_sl < self.sl_price or self.sl_price <= 0:
                    self.sl_price = new_sl

    def _evaluate_exit(self, ctx: StrategyContext, price: float) -> bool:
        if ctx.position_size > 0:
            if self.sl_price > 0 and price <= self.sl_price:
                self._close(
                    ctx,
                    f"SL Long {price:.2f}<={self.sl_price:.2f}",
                    exit_reason="TRAIL_STOP" if self.trailing_active else "STOP_LOSS",
                )
                return True
            if self.tp_price > 0 and price >= self.tp_price:
                self._close(
                    ctx,
                    f"TP Long {price:.2f}>={self.tp_price:.2f}",
                    exit_reason="TAKE_PROFIT",
                )
                return True
        elif ctx.position_size < 0:
            if self.sl_price > 0 and price >= self.sl_price:
                self._close(
                    ctx,
                    f"SL Short {price:.2f}>={self.sl_price:.2f}",
                    exit_reason="TRAIL_STOP" if self.trailing_active else "STOP_LOSS",
                )
                return True
            if self.tp_price > 0 and price <= self.tp_price:
                self._close(
                    ctx,
                    f"TP Short {price:.2f}<={self.tp_price:.2f}",
                    exit_reason="TAKE_PROFIT",
                )
                return True
        return False

    # ------------------------------------------------------------------ #
    # 손실 streak 갱신
    # ------------------------------------------------------------------ #

    def _update_loss_streak(self, ctx: StrategyContext) -> None:
        for trade in reversed(getattr(ctx, "trades", [])):
            if "pnl" in trade:
                pnl = float(trade.get("pnl", 0.0))
                if pnl < 0:
                    self._consecutive_losses += 1
                else:
                    self._consecutive_losses = 0
                break
        if self._consecutive_losses >= self.consecutive_loss_max:
            extra = self.cooldown_bars * self.loss_pause_multiplier
            self._extra_pause_until_bar = self._bar_counter + extra
            self._consecutive_losses = 0
