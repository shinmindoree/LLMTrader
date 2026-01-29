"""RSI 기반 1분봉 초단타 롱 전략.

설계 목표:
- 1분봉에서 극단적인 과매도 구간만 아주 짧게 스캘핑하는 고승률(mean-reversion) 전략
- 진입은 공격적으로, 청산은 보수적으로 잡아 승률을 높이고, 빈도는 줄이는 방향

주의:
- **백테스트/실거래 데이터에 따라 실제 승률 90%는 보장되지 않는다.**
- 이 코드는 \"높은 승률을 지향하는 구조\"로 설계되었을 뿐, 필수적으로 실전 데이터로 검증해야 한다.
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
    """TA-Lib builtin 인디케이터를 \"가능하면 모든 output을 dict로\" 반환하도록 오버라이드.

    - single-output: float
    - multi-output: dict[str, float]
    """

    try:
        import numpy as np  # type: ignore
        abstract = importlib.import_module("talib.abstract")
    except Exception:  # noqa: BLE001
        return

    def _indicator(inner_ctx: Any, *args: Any, **kwargs: Any) -> Any:
        output = kwargs.pop("output", None)
        output_index = kwargs.pop("output_index", None)

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
            values: list[float] = []
            for series in series_list:
                v = _last_non_nan(series)
                values.append(float(v) if v is not None else math.nan)
            names = (
                ["macd", "macdsignal", "macdhist"][: len(values)]
                if normalized == "MACD"
                else [f"output_{i}" for i in range(len(values))]
            )
            if output is not None:
                try:
                    return values[names.index(str(output))]
                except ValueError:
                    return math.nan
            if output_index is not None:
                idx = int(output_index)
                return values[idx] if 0 <= idx < len(values) else math.nan
            return {names[i]: values[i] for i in range(len(values))}

        v = _last_non_nan(result)
        return float(v) if v is not None else math.nan

    ctx.register_indicator(name, _indicator)


def crossed_above(prev: float, current: float, level: float) -> bool:
    """prev < level <= current"""
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    """current <= level < prev"""
    return current <= level < prev


class RsiMicroScalpingLongStrategy(Strategy):
    """RSI 과매도 극단 구간만 노리는 1분봉 초단타 롱 전략.

    아이디어:
    - 진입: RSI가 매우 낮은 구간(예: 15 이하)에서 반등하며 oversold 레벨(예: 20)을 상향 돌파할 때만 진입
    - 청산: RSI가 중립~과열 초입 구간(예: 55~60)을 상향 돌파하면 청산 → 작은 이익을 빠르게 실현
    - 추가 안전장치:
      - RSI가 다시 극단적으로 무너질 경우(예: 10 하향 돌파) 강제 손절
      - 최근 N봉 내에 이미 진입/청산이 있었으면 재진입 쿨다운
    - 1분봉 기준으로 가정하지만, 실제 타임프레임은 스트림 설정에서 결정된다.
    """

    INDICATOR_NAME = "RSI"

    def __init__(
        self,
        period: int = 14,
        oversold_level: float = 20.0,
        extreme_oversold_level: float = 10.0,
        takeprofit_level: float = 58.0,
        cooldown_bars: int = 5,
    ) -> None:
        super().__init__()
        if period <= 1:
            raise ValueError("period must be > 1")
        if extreme_oversold_level >= oversold_level:
            raise ValueError("extreme_oversold_level must be < oversold_level")

        self.period = int(period)
        self.oversold_level = float(oversold_level)
        self.extreme_oversold_level = float(extreme_oversold_level)
        self.takeprofit_level = float(takeprofit_level)
        self.cooldown_bars = int(cooldown_bars)

        # 상태값
        self.prev_rsi: float | None = None
        self.is_closing: bool = False
        self.cooldown_counter: int = 0  # 0보다 크면 신규 진입 금지

        # 로그/메타용
        self.params = {
            "period": self.period,
            "oversold_level": self.oversold_level,
            "extreme_oversold_level": self.extreme_oversold_level,
            "takeprofit_level": self.takeprofit_level,
            "cooldown_bars": self.cooldown_bars,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {"period": self.period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        # multi-output 인디케이터(MACD 등)도 로그에 \"전체 output\"이 찍히도록 표준화.
        register_talib_indicator_all_outputs(ctx, self.INDICATOR_NAME)
        self.prev_rsi = None
        self.is_closing = False
        self.cooldown_counter = 0

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 및 쿨다운 감소 =====
        if ctx.position_size == 0:
            self.is_closing = False
        if self.cooldown_counter > 0 and bool(bar.get("is_new_bar", True)):
            self.cooldown_counter -= 1

        # ===== 미체결 주문 가드(라이브 전용) =====
        if ctx.get_open_orders():
            return

        # 새 봉이 확정된 시점에서만 크로스 판단/prev 갱신 (백테스트 stoploss 시뮬레이션 호환)
        if not bool(bar.get("is_new_bar", True)):
            return

        rsi = float(ctx.get_indicator(self.INDICATOR_NAME, period=self.period))

        if not math.isfinite(rsi):
            return

        if self.prev_rsi is None or not math.isfinite(self.prev_rsi):
            self.prev_rsi = rsi
            return

        # ===== (1) 롱 포지션 강제 손절: RSI가 극단 구간을 다시 하향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if crossed_below(self.prev_rsi, rsi, self.extreme_oversold_level):
                # 극단 저점 재이탈: 리스크 축소를 위해 강제 청산
                self.is_closing = True
                self.cooldown_counter = self.cooldown_bars
                ctx.close_position(reason=f"Emergency Stop (RSI {self.prev_rsi:.2f} -> {rsi:.2f})")
                self.prev_rsi = rsi
                return

        # ===== (2) 롱 포지션 일반 청산: RSI가 takeprofit_level 상향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_rsi, rsi, self.takeprofit_level):
                self.is_closing = True
                self.cooldown_counter = self.cooldown_bars
                ctx.close_position(reason=f"Take Profit (RSI {self.prev_rsi:.2f} -> {rsi:.2f})")
                self.prev_rsi = rsi
                return

        # ===== (3) 롱 진입: RSI가 극단 과매도 후 oversold_level 상향 돌파 =====
        if ctx.position_size == 0 and self.cooldown_counter == 0:
            # prev_rsi가 extreme_oversold_level 아래에 있었고, 현재는 oversold_level 위로 반등
            if self.prev_rsi <= self.extreme_oversold_level and crossed_above(self.prev_rsi, rsi, self.oversold_level):
                ctx.enter_long(
                    reason=f"RSI micro scalp entry (RSI {self.prev_rsi:.2f} -> {rsi:.2f}, "
                    f"oversold<{self.oversold_level})"
                )

        self.prev_rsi = rsi

