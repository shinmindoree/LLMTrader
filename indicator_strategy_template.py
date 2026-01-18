"""인디케이터 기반 전략 템플릿.

목적:
- `scripts/run_live_trading.py` / 백테스트 엔진에서 바로 로드 가능한 "전략 파일 포맷" 표준화
- RSI뿐 아니라 다른 인디케이터 기반 전략도 같은 뼈대(guard → 지표조회 → 신호판단 → 상태업데이트)로 생성

사용법:
1) 이 파일을 복사해서 `my_strategy.py`로 저장
2) 클래스 이름을 `SomethingStrategy` 형태로 변경 (loader가 `*Strategy` 클래스를 찾음)
3) builtin 인디케이터를 쓸 경우 `INDICATOR_NAME`만 바꾸고 `ctx.get_indicator(...)`로 호출
4) custom 인디케이터를 쓸 경우 `initialize()`에서 `ctx.register_indicator(name, func)`로 등록
5) `on_bar()`의 진입/청산 조건을 수정

연동 포인트(현재 시스템 기준):
- 주문/리스크/StopLoss/수량 산정은 컨텍스트(ctx)가 담당 → 전략은 신호만 생성
- 크로스 판단/prev 업데이트는 `bar["is_new_bar"] == True` 에서만 수행 (백테스트 stoploss 시뮬레이션 호환)
- 라이브에서 중복 주문 방지: 미체결 주문이 있으면 신호 무시(`ctx.get_open_orders()` 가드)
"""

from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path
from typing import Any

# 전략 파일을 단독 실행/로드할 때도 `src/` 임포트가 되도록 보정.
# (run_live_trading.py가 이미 sys.path에 src를 추가하지만, 다른 실행 경로 대비)
project_root = Path(__file__).parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

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
    """TA-Lib builtin 인디케이터를 "가능하면 모든 output을 dict로" 반환하도록 오버라이드한다.

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


class IndicatorLongShortStrategyTemplate(Strategy):
    """인디케이터 기반 롱/숏 전략 템플릿.

    기본 구현은 TA-Lib builtin RSI(oscillator) 예시로 채워져 있습니다.

    다른 인디케이터로 교체할 때는 아래만 바꾸면 됩니다:
    - `INDICATOR_NAME`
    - `on_bar()`의 신호 조건

    custom 인디케이터를 쓰고 싶다면 `initialize()`에서 `ctx.register_indicator(...)`로 등록하고,
    `INDICATOR_NAME`에 그 이름을 설정하세요.
    """

    INDICATOR_NAME = "RSI"

    def __init__(
        self,
        period: int = 14,
        long_entry_level: float = 30.0,
        long_exit_level: float = 70.0,
        short_entry_level: float = 70.0,
        short_exit_level: float = 30.0,
    ) -> None:
        super().__init__()
        if period <= 1:
            raise ValueError("period must be > 1")

        self.period = int(period)
        self.long_entry_level = float(long_entry_level)
        self.long_exit_level = float(long_exit_level)
        self.short_entry_level = float(short_entry_level)
        self.short_exit_level = float(short_exit_level)

        # 상태값: "마지막 확정 봉"에서 계산된 인디케이터 값
        self.prev_value: float | None = None
        self.is_closing: bool = False  # 청산 주문 진행 중 플래그(중복 청산 방지)

        # 로그/메타용(컨텍스트가 읽어서 저장할 수 있음)
        self.params = {
            "period": self.period,
            "long_entry_level": self.long_entry_level,
            "long_exit_level": self.long_exit_level,
            "short_entry_level": self.short_entry_level,
            "short_exit_level": self.short_exit_level,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {"period": self.period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        # multi-output 인디케이터(MACD 등)도 로그에 "전체 output"이 찍히도록 표준화.
        register_talib_indicator_all_outputs(ctx, self.INDICATOR_NAME)
        self.prev_value = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드(라이브 전용) =====
        open_orders = getattr(ctx, "get_open_orders", lambda: [])()
        if open_orders:
            return

        # 새 봉이 확정된 시점에서만 크로스 판단/prev 갱신 (백테스트 stoploss 시뮬레이션 호환)
        if not bool(bar.get("is_new_bar", True)):
            return

        value = float(ctx.get_indicator(self.INDICATOR_NAME, period=self.period))

        if not math.isfinite(value):
            return

        if self.prev_value is None or not math.isfinite(self.prev_value):
            self.prev_value = value
            return

        # ===== (예시) 롱 포지션 청산: value가 long_exit_level 상향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if crossed_above(self.prev_value, value, self.long_exit_level):
                self.is_closing = True
                ctx.close_position(reason=f"Exit Long ({self.prev_value:.2f} -> {value:.2f})")
                self.prev_value = value
                return

        # ===== (예시) 숏 포지션 청산: value가 short_exit_level 하향 돌파 =====
        if ctx.position_size < 0 and not self.is_closing:
            if crossed_below(self.prev_value, value, self.short_exit_level):
                self.is_closing = True
                ctx.close_position(reason=f"Exit Short ({self.prev_value:.2f} -> {value:.2f})")
                self.prev_value = value
                return

        # ===== (예시) 롱 진입: value가 long_entry_level 상향 돌파 =====
        if ctx.position_size == 0:
            if crossed_above(self.prev_value, value, self.long_entry_level):
                ctx.enter_long(reason=f"Entry Long ({self.prev_value:.2f} -> {value:.2f})")

        # ===== (예시) 숏 진입: value가 short_entry_level 하향 돌파 =====
        if ctx.position_size == 0:
            if crossed_below(self.prev_value, value, self.short_entry_level):
                ctx.enter_short(reason=f"Entry Short ({self.prev_value:.2f} -> {value:.2f})")

        self.prev_value = value
