"""RSI + Pivot Point 지지/저항 + 캔들 패턴 확인 롱/숏 전략.

rsi_long_short_strategy.py 를 기반으로 2가지 필터를 추가:
1) Pivot Point 지지/저항 근접 확인 (가격이 S1/R1 부근일 때만 진입)
2) 강세/약세 캔들 패턴 확인 (Engulfing, Hammer, Shooting Star 등)

규칙:
- 롱 진입: RSI가 long_entry_rsi 상향 돌파
            AND 가격이 Pivot S1 이하 또는 S1 부근(proximity% 이내)
            AND 강세 캔들 패턴 출현 (Engulfing/Hammer/Morning Star 중 하나)
- 숏 진입: RSI가 short_entry_rsi 하향 돌파
            AND 가격이 Pivot R1 이상 또는 R1 부근(proximity% 이내)
            AND 약세 캔들 패턴 출현 (Engulfing/Shooting Star/Evening Star 중 하나)
- 롱 청산: RSI가 long_exit_rsi 상향 돌파
- 숏 청산: RSI가 short_exit_rsi 하향 돌파
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


def _register_pivot_points(ctx: StrategyContext) -> None:
    """Rolling Pivot Point 커스텀 인디케이터 등록.

    직전 N봉의 high/low/close로 Pivot, S1, S2, R1, R2를 계산.
    - Pivot = (H + L + C) / 3
    - R1 = 2*P - L,  R2 = P + (H - L)
    - S1 = 2*P - H,  S2 = P - (H - L)

    Returns dict: {"pivot", "r1", "r2", "s1", "s2"}
    """
    try:
        import numpy as np  # type: ignore
    except Exception:  # noqa: BLE001
        return

    def _pivot(inner_ctx: Any, *args: Any, **kwargs: Any) -> dict[str, float]:
        period = int(kwargs.get("period", kwargs.get("timeperiod", 20)))

        inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
        if not callable(inputs):
            return {k: float("nan") for k in ("pivot", "r1", "r2", "s1", "s2")}
        raw = inputs()
        high = np.asarray(list(raw.get("high", [])), dtype="float64")
        low = np.asarray(list(raw.get("low", [])), dtype="float64")
        close = np.asarray(list(raw.get("close", [])), dtype="float64")

        n = len(close)
        if n < period:
            return {k: float("nan") for k in ("pivot", "r1", "r2", "s1", "s2")}

        h = float(np.max(high[-period:]))
        l_val = float(np.min(low[-period:]))
        c = float(close[-1])

        pivot = (h + l_val + c) / 3.0
        r1 = 2.0 * pivot - l_val
        r2 = pivot + (h - l_val)
        s1 = 2.0 * pivot - h
        s2 = pivot - (h - l_val)

        return {"pivot": pivot, "r1": r1, "r2": r2, "s1": s1, "s2": s2}

    ctx.register_indicator("PIVOT", _pivot)


def crossed_above(prev: float, current: float, level: float) -> bool:
    """prev < level <= current"""
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    """current <= level < prev"""
    return current <= level < prev


# ===== 강세 캔들 패턴 함수명 목록 (TA-Lib) =====
_BULLISH_CDL_PATTERNS = [
    "CDLENGULFING",      # Bullish Engulfing (+100)
    "CDLHAMMER",         # Hammer (+100)
    "CDLMORNINGSTAR",    # Morning Star (+100)
    "CDLPIERCING",       # Piercing Line (+100)
    "CDLHARAMI",         # Bullish Harami (+100)
    "CDLDRAGONFLYDOJI",  # Dragonfly Doji (+100)
]

# ===== 약세 캔들 패턴 함수명 목록 (TA-Lib) =====
_BEARISH_CDL_PATTERNS = [
    "CDLENGULFING",         # Bearish Engulfing (-100)
    "CDLSHOOTINGSTAR",      # Shooting Star (-100)
    "CDLEVENINGSTAR",       # Evening Star (-100)
    "CDLDARKCLOUDCOVER",    # Dark Cloud Cover (-100)
    "CDLHARAMI",            # Bearish Harami (-100)
    "CDLGRAVESTONEDOJI",    # Gravestone Doji (-100)
]


STRATEGY_PARAMS: dict[str, Any] = {
    "rsi_period": 14,
    "long_entry_rsi": 30.0,
    "long_exit_rsi": 70.0,
    "short_entry_rsi": 70.0,
    "short_exit_rsi": 30.0,
    "pivot_period": 20,
    "sr_proximity_pct": 0.5,
    "require_candle_pattern": True,
    "require_sr_filter": True,
}

STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "rsi_period": {
        "type": "integer", "min": 2, "max": 100,
        "label": "RSI 기간",
        "description": "RSI 계산에 사용할 캔들 수",
        "group": "지표 (Indicator)",
    },
    "long_entry_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "롱 진입 RSI",
        "description": "RSI가 이 값을 상향 돌파하면 롱 진입 조건 충족",
        "group": "진입 (Entry)",
    },
    "long_exit_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "롱 청산 RSI",
        "description": "RSI가 이 값을 상향 돌파하면 롱 포지션 청산",
        "group": "청산 (Exit)",
    },
    "short_entry_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "숏 진입 RSI",
        "description": "RSI가 이 값을 하향 돌파하면 숏 진입 조건 충족",
        "group": "진입 (Entry)",
    },
    "short_exit_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "숏 청산 RSI",
        "description": "RSI가 이 값을 하향 돌파하면 숏 포지션 청산",
        "group": "청산 (Exit)",
    },
    "pivot_period": {
        "type": "integer", "min": 5, "max": 200,
        "label": "Pivot 기간",
        "description": "지지/저항 계산에 사용할 봉 수 (기본 20)",
        "group": "지지/저항 (S/R)",
    },
    "sr_proximity_pct": {
        "type": "number", "min": 0.05, "max": 3.0,
        "label": "S/R 근접 %",
        "description": "가격이 S1/R1에서 이 퍼센트 이내일 때 '근접'으로 판단",
        "group": "지지/저항 (S/R)",
    },
    "require_candle_pattern": {
        "type": "boolean",
        "label": "캔들 패턴 필터",
        "description": "True면 강세/약세 캔들 패턴이 확인될 때만 진입",
        "group": "필터 (Filter)",
    },
    "require_sr_filter": {
        "type": "boolean",
        "label": "지지/저항 필터",
        "description": "True면 가격이 S1/R1 부근일 때만 진입",
        "group": "필터 (Filter)",
    },
}


class RsiPivotCandleStrategy(Strategy):
    """RSI + Pivot Point 지지/저항 + 캔들 패턴 확인 롱/숏 전략.

    진입 (3중 필터):
    1. RSI 크로스 (기본 진입 신호)
    2. Pivot Point 지지/저항 근접 확인 (가격 위치 필터)
    3. 강세/약세 캔들 패턴 확인 (진입 타이밍 확인)

    롱 진입: RSI 30 상향돌파 + 가격≈S1(지지선) + 강세 캔들 패턴
    숏 진입: RSI 70 하향돌파 + 가격≈R1(저항선) + 약세 캔들 패턴

    청산: RSI 기반 (기존 rsi_long_short 로직 동일)
    """

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
        self.pivot_period = int(p["pivot_period"])
        self.sr_proximity_pct = float(p["sr_proximity_pct"]) / 100.0  # 0.5% -> 0.005
        self.require_candle_pattern = bool(p["require_candle_pattern"])
        self.require_sr_filter = bool(p["require_sr_filter"])

        self.prev_rsi: float | None = None
        self.is_closing: bool = False

        self.params = {
            "rsi_period": self.rsi_period,
            "long_entry_rsi": self.long_entry_rsi,
            "long_exit_rsi": self.long_exit_rsi,
            "short_entry_rsi": self.short_entry_rsi,
            "short_exit_rsi": self.short_exit_rsi,
            "pivot_period": self.pivot_period,
            "sr_proximity_pct": self.sr_proximity_pct * 100.0,
            "require_candle_pattern": self.require_candle_pattern,
            "require_sr_filter": self.require_sr_filter,
        }
        self.indicator_config = {
            "RSI": {"period": self.rsi_period},
            "PIVOT": {"period": self.pivot_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        register_talib_indicator_all_outputs(ctx, "RSI")
        # 캔들 패턴 인디케이터 등록 (TA-Lib CDL* 함수 동적 호출)
        all_cdl = set(_BULLISH_CDL_PATTERNS) | set(_BEARISH_CDL_PATTERNS)
        for cdl_name in all_cdl:
            register_talib_indicator_all_outputs(ctx, cdl_name)
        # Pivot Point 커스텀 인디케이터 등록
        _register_pivot_points(ctx)
        self.prev_rsi = None
        self.is_closing = False

    def _near_support(self, price: float, s1: float, s2: float) -> bool:
        """가격이 S1 또는 S2 근처(proximity 이내)이거나 그 아래인지 확인."""
        if not math.isfinite(s1):
            return False
        if price <= s1:
            return True
        if abs(price - s1) / price <= self.sr_proximity_pct:
            return True
        if math.isfinite(s2) and price <= s2:
            return True
        return False

    def _near_resistance(self, price: float, r1: float, r2: float) -> bool:
        """가격이 R1 또는 R2 근처(proximity 이내)이거나 그 위인지 확인."""
        if not math.isfinite(r1):
            return False
        if price >= r1:
            return True
        if abs(price - r1) / price <= self.sr_proximity_pct:
            return True
        if math.isfinite(r2) and price >= r2:
            return True
        return False

    def _has_bullish_candle(self, ctx: StrategyContext) -> tuple[bool, str]:
        """강세 캔들 패턴 중 하나라도 감지되면 True + 패턴명 반환."""
        for pattern_name in _BULLISH_CDL_PATTERNS:
            try:
                val = float(ctx.get_indicator(pattern_name))
            except Exception:  # noqa: BLE001
                continue
            if math.isfinite(val) and val > 0:
                return True, pattern_name
        return False, ""

    def _has_bearish_candle(self, ctx: StrategyContext) -> tuple[bool, str]:
        """약세 캔들 패턴 중 하나라도 감지되면 True + 패턴명 반환."""
        for pattern_name in _BEARISH_CDL_PATTERNS:
            try:
                val = float(ctx.get_indicator(pattern_name))
            except Exception:  # noqa: BLE001
                continue
            if math.isfinite(val) and val < 0:
                return True, pattern_name
        return False, ""

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드 =====
        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        if not bool(bar.get("is_new_bar", True)):
            return

        rsi = float(ctx.get_indicator("RSI", period=self.rsi_period))
        if not math.isfinite(rsi):
            return

        if self.prev_rsi is None or not math.isfinite(self.prev_rsi):
            self.prev_rsi = rsi
            return

        # ===== 롱 포지션 청산: RSI long_exit_rsi 상향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_rsi, rsi, self.long_exit_rsi):
                self.is_closing = True
                ctx.close_position(reason=f"RSI Exit Long ({self.prev_rsi:.1f} -> {rsi:.1f})")
                self.prev_rsi = rsi
                return

        # ===== 숏 포지션 청산: RSI short_exit_rsi 하향 돌파 =====
        if ctx.position_size < 0 and not self.is_closing:
            if crossed_below(self.prev_rsi, rsi, self.short_exit_rsi):
                self.is_closing = True
                ctx.close_position(reason=f"RSI Exit Short ({self.prev_rsi:.1f} -> {rsi:.1f})")
                self.prev_rsi = rsi
                return

        # ===== 진입: 3중 필터 =====
        if ctx.position_size == 0:
            price = ctx.current_price

            # Pivot Point 조회
            pivot_data: dict[str, float] = {"pivot": math.nan, "r1": math.nan, "r2": math.nan, "s1": math.nan, "s2": math.nan}
            if self.require_sr_filter:
                raw = ctx.get_indicator("PIVOT", period=self.pivot_period)
                if isinstance(raw, dict):
                    pivot_data = raw

            # ===== 롱 진입 =====
            if crossed_above(self.prev_rsi, rsi, self.long_entry_rsi):
                # 필터 1: 지지선 근접 확인
                sr_ok = True
                sr_info = ""
                if self.require_sr_filter:
                    sr_ok = self._near_support(price, pivot_data["s1"], pivot_data["s2"])
                    sr_info = f" S1={pivot_data['s1']:.2f}" if math.isfinite(pivot_data["s1"]) else ""

                # 필터 2: 강세 캔들 패턴 확인
                cdl_ok = True
                cdl_info = ""
                if self.require_candle_pattern:
                    cdl_ok, cdl_name = self._has_bullish_candle(ctx)
                    cdl_info = f" CDL={cdl_name}" if cdl_ok else ""

                if sr_ok and cdl_ok:
                    ctx.enter_long(
                        reason=f"RSI Long ({self.prev_rsi:.1f}->{rsi:.1f}){sr_info}{cdl_info}",
                    )

            # ===== 숏 진입 =====
            elif crossed_below(self.prev_rsi, rsi, self.short_entry_rsi):
                # 필터 1: 저항선 근접 확인
                sr_ok = True
                sr_info = ""
                if self.require_sr_filter:
                    sr_ok = self._near_resistance(price, pivot_data["r1"], pivot_data["r2"])
                    sr_info = f" R1={pivot_data['r1']:.2f}" if math.isfinite(pivot_data["r1"]) else ""

                # 필터 2: 약세 캔들 패턴 확인
                cdl_ok = True
                cdl_info = ""
                if self.require_candle_pattern:
                    cdl_ok, cdl_name = self._has_bearish_candle(ctx)
                    cdl_info = f" CDL={cdl_name}" if cdl_ok else ""

                if sr_ok and cdl_ok:
                    ctx.enter_short(
                        reason=f"RSI Short ({self.prev_rsi:.1f}->{rsi:.1f}){sr_info}{cdl_info}",
                    )

        self.prev_rsi = rsi
