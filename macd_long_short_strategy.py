"""MACD 기반 롱/숏 전략.

포맷:
- `indicator_strategy_template.py`의 구조(guard → 지표조회 → 신호판단 → 상태업데이트)를 그대로 따릅니다.

규칙(기본값):
- MACD histogram(macd - signal)이 0을 상향 돌파하면 롱 진입
- MACD histogram이 0을 하향 돌파하면 숏 진입
- 롱 포지션 보유 중 histogram이 0을 하향 돌파하면 청산
- 숏 포지션 보유 중 histogram이 0을 상향 돌파하면 청산

참고:
- StopLoss/수량 산정은 컨텍스트(ctx)가 담당 → 전략은 신호만 생성
- 크로스 판단/prev 업데이트는 `bar["is_new_bar"] == True` 에서만 수행 (백테스트 stoploss 시뮬레이션 호환)
- 라이브에서 중복 주문 방지: 미체결 주문이 있으면 신호 무시(`ctx.get_open_orders()` 가드)
"""

from __future__ import annotations

import math
from typing import Any

from indicators.builtin import compute as compute_builtin_indicator
from strategy.base import Strategy
from strategy.context import StrategyContext


def crossed_above(prev: float, current: float, level: float) -> bool:
    """prev < level <= current"""
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    """current <= level < prev"""
    return current <= level < prev


def _as_macd_dict(result: Any) -> dict[str, float]:
    """MACD 결과를 {macd, macdsignal, macdhist} dict로 정규화."""
    if isinstance(result, dict):
        out: dict[str, float] = {}
        for key in ("macd", "macdsignal", "macdhist"):
            if key in result:
                out[key] = float(result[key])
            elif key.upper() in result:
                out[key] = float(result[key.upper()])
            else:
                out[key] = float("nan")
        return out

    if isinstance(result, (list, tuple)) and len(result) >= 3:
        return {
            "macd": float(result[0]),
            "macdsignal": float(result[1]),
            "macdhist": float(result[2]),
        }

    return {"macd": float("nan"), "macdsignal": float("nan"), "macdhist": float("nan")}


class MacdLongShortStrategy(Strategy):
    """MACD histogram 0 크로스 기반 롱/숏 전략."""

    INDICATOR_NAME = "MACD"

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> None:
        super().__init__()
        if fast_period <= 0:
            raise ValueError("fast_period must be > 0")
        if slow_period <= 0:
            raise ValueError("slow_period must be > 0")
        if signal_period <= 0:
            raise ValueError("signal_period must be > 0")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")

        self.fast_period = int(fast_period)
        self.slow_period = int(slow_period)
        self.signal_period = int(signal_period)

        # 상태값: "마지막 확정 봉"에서 계산된 histogram 값
        self.prev_hist: float | None = None
        self.is_closing: bool = False  # 청산 주문 진행 중 플래그(중복 청산 방지)

        # 로그/메타용(컨텍스트가 읽어서 저장할 수 있음)
        self.params = {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "signal_period": self.signal_period,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "fastperiod": self.fast_period,
                "slowperiod": self.slow_period,
                "signalperiod": self.signal_period,
            },
        }

    def initialize(self, ctx: StrategyContext) -> None:
        def macd_indicator(
            inner_ctx: Any,
            *,
            fastperiod: int = 12,
            slowperiod: int = 26,
            signalperiod: int = 9,
            **_kwargs: Any,
        ) -> dict[str, float]:
            inputs = getattr(inner_ctx, "_get_builtin_indicator_inputs", None)
            if not callable(inputs):
                return {"macd": float("nan"), "macdsignal": float("nan"), "macdhist": float("nan")}
            result = compute_builtin_indicator(
                "MACD",
                inputs(),
                fastperiod=int(fastperiod),
                slowperiod=int(slowperiod),
                signalperiod=int(signalperiod),
            )
            if isinstance(result, dict):
                return _as_macd_dict(result)

            # 일부 환경에서 MACD가 tuple/list로 반환되면, builtin wrapper가 기본으로 "첫 output(float)"만 반환한다.
            # 이 경우 output_index로 각 output을 개별 조회해 dict로 조립한다.
            inputs_data = inputs()
            macd = float(
                compute_builtin_indicator(
                    "MACD",
                    inputs_data,
                    fastperiod=int(fastperiod),
                    slowperiod=int(slowperiod),
                    signalperiod=int(signalperiod),
                    output_index=0,
                )
            )
            macdsignal = float(
                compute_builtin_indicator(
                    "MACD",
                    inputs_data,
                    fastperiod=int(fastperiod),
                    slowperiod=int(slowperiod),
                    signalperiod=int(signalperiod),
                    output_index=1,
                )
            )
            macdhist = float(
                compute_builtin_indicator(
                    "MACD",
                    inputs_data,
                    fastperiod=int(fastperiod),
                    slowperiod=int(slowperiod),
                    signalperiod=int(signalperiod),
                    output_index=2,
                )
            )
            return {"macd": macd, "macdsignal": macdsignal, "macdhist": macdhist}

        # 일부 환경에서는 TA-Lib MACD가 tuple/list로 반환되어 output="macdhist"가 동작하지 않는다.
        # 로그/전략에서 일관된 형태를 위해 "MACD" 지표를 dict 반환으로 오버라이드한다.
        ctx.register_indicator(self.INDICATOR_NAME, macd_indicator)

        self.prev_hist = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드(라이브 전용) =====
        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        # 새 봉이 확정된 시점에서만 크로스 판단/prev 갱신 (백테스트 stoploss 시뮬레이션 호환)
        if not bool(bar.get("is_new_bar", True)):
            return

        macd = ctx.get_indicator(
            self.INDICATOR_NAME,
            fastperiod=self.fast_period,
            slowperiod=self.slow_period,
            signalperiod=self.signal_period,
        )
        hist = float(macd.get("macdhist", float("nan"))) if isinstance(macd, dict) else float("nan")

        if not math.isfinite(hist):
            return

        if self.prev_hist is None or not math.isfinite(self.prev_hist):
            self.prev_hist = hist
            return

        # ===== 롱 포지션 청산: histogram 0 하향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if crossed_below(self.prev_hist, hist, 0.0):
                self.is_closing = True
                ctx.close_position(reason=f"MACD Exit Long ({self.prev_hist:.6f} -> {hist:.6f})")
                self.prev_hist = hist
                return

        # ===== 숏 포지션 청산: histogram 0 상향 돌파 =====
        if ctx.position_size < 0 and not self.is_closing:
            if crossed_above(self.prev_hist, hist, 0.0):
                self.is_closing = True
                ctx.close_position(reason=f"MACD Exit Short ({self.prev_hist:.6f} -> {hist:.6f})")
                self.prev_hist = hist
                return

        # ===== 롱 진입: histogram 0 상향 돌파 =====
        if ctx.position_size == 0:
            if crossed_above(self.prev_hist, hist, 0.0):
                ctx.enter_long(reason=f"MACD Entry Long ({self.prev_hist:.6f} -> {hist:.6f})")

        # ===== 숏 진입: histogram 0 하향 돌파 =====
        if ctx.position_size == 0:
            if crossed_below(self.prev_hist, hist, 0.0):
                ctx.enter_short(reason=f"MACD Entry Short ({self.prev_hist:.6f} -> {hist:.6f})")

        self.prev_hist = hist
