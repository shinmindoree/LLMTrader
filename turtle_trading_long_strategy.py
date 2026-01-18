"""터틀 트레이딩(롱 온리) 전략.

포맷:
- `indicator_strategy_template.py`의 구조(guard → 지표조회 → 신호판단 → 상태업데이트)를 따릅니다.

규칙(기본값):
- 진입: 현재 봉의 high가 "직전 entry_period 봉"의 최고가(Donchian High)를 돌파하면 롱 진입
- 청산: 현재 봉의 low가 "직전 exit_period 봉"의 최저가(Donchian Low)를 하향 돌파하면 청산

참고:
- StopLoss/수량 산정은 컨텍스트(ctx)가 담당 → 전략은 신호만 생성
- 크로스 판단/prev 업데이트는 `bar["is_new_bar"] == True` 에서만 수행 (백테스트 stoploss 시뮬레이션 호환)
- 라이브에서 중복 주문 방지: 미체결 주문이 있으면 신호 무시(`ctx.get_open_orders()` 가드)
"""

from __future__ import annotations

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


class TurtleTradingLongStrategy(Strategy):
    """터틀 트레이딩(Donchian) 기반 롱 온리 전략."""

    ENTRY_INDICATOR_NAME = "MAX"
    EXIT_INDICATOR_NAME = "MIN"

    def __init__(
        self,
        entry_period: int = 20,
        exit_period: int = 10,
    ) -> None:
        super().__init__()
        if entry_period <= 1:
            raise ValueError("entry_period must be > 1")
        if exit_period <= 0:
            raise ValueError("exit_period must be > 0")

        self.entry_period = int(entry_period)
        self.exit_period = int(exit_period)

        # 상태값: "마지막 확정 봉" 기준 Donchian 레벨(다음 봉에서 사용)
        self.prev_entry_high: float | None = None
        self.prev_exit_low: float | None = None
        self.is_closing: bool = False  # 청산 주문 진행 중 플래그(중복 청산 방지)

        # 로그/메타용(컨텍스트가 읽어서 저장할 수 있음)
        self.params = {
            "entry_period": self.entry_period,
            "exit_period": self.exit_period,
        }
        self.indicator_config = {
            # TA-Lib abstract는 `price`로 high/low 시리즈 선택 가능(미지원 환경이면 close로 fallback 가능)
            self.ENTRY_INDICATOR_NAME: {"timeperiod": self.entry_period, "price": "high"},
            self.EXIT_INDICATOR_NAME: {"timeperiod": self.exit_period, "price": "low"},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        self.prev_entry_high = None
        self.prev_exit_low = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드(라이브 전용) =====
        open_orders = getattr(ctx, "get_open_orders", lambda: [])()
        if open_orders:
            return

        # 새 봉이 확정된 시점에서만 신호 판단/prev 갱신 (백테스트 stoploss 시뮬레이션 호환)
        if not bool(bar.get("is_new_bar", True)):
            return

        # Donchian 채널 계산(현재 봉 포함). 다음 봉에서 사용할 "직전 값"은 prev_*에 저장.
        try:
            entry_high = float(
                ctx.get_indicator(
                    self.ENTRY_INDICATOR_NAME,
                    timeperiod=self.entry_period,
                    price="high",
                )
            )
            exit_low = float(
                ctx.get_indicator(
                    self.EXIT_INDICATOR_NAME,
                    timeperiod=self.exit_period,
                    price="low",
                )
            )
        except TypeError:
            # 일부 환경에서 `price=` 미지원 시 close 기준으로 fallback
            entry_high = float(ctx.get_indicator(self.ENTRY_INDICATOR_NAME, timeperiod=self.entry_period))
            exit_low = float(ctx.get_indicator(self.EXIT_INDICATOR_NAME, timeperiod=self.exit_period))

        if not (math.isfinite(entry_high) and math.isfinite(exit_low)):
            return

        if (
            self.prev_entry_high is None
            or self.prev_exit_low is None
            or not (math.isfinite(self.prev_entry_high) and math.isfinite(self.prev_exit_low))
        ):
            self.prev_entry_high = entry_high
            self.prev_exit_low = exit_low
            return

        bar_high = float(bar.get("high", bar.get("close", ctx.current_price)))
        bar_low = float(bar.get("low", bar.get("close", ctx.current_price)))

        # ===== 롱 온리 강제: 숏 포지션이면 즉시 청산 시도 =====
        if ctx.position_size < 0 and not self.is_closing:
            self.is_closing = True
            ctx.close_position(reason="Long-only: close short")
            self.prev_entry_high = entry_high
            self.prev_exit_low = exit_low
            return

        # ===== 롱 포지션 청산: 직전 exit_period 최저가 하향 돌파 =====
        if ctx.position_size > 0 and not self.is_closing:
            if bar_low < self.prev_exit_low:
                self.is_closing = True
                ctx.close_position(
                    reason=f"Turtle Exit (low {bar_low:.2f} < {self.prev_exit_low:.2f})",
                )
                self.prev_entry_high = entry_high
                self.prev_exit_low = exit_low
                return

        # ===== 롱 진입: 직전 entry_period 최고가 상향 돌파 =====
        if ctx.position_size == 0:
            if bar_high > self.prev_entry_high:
                ctx.enter_long(
                    reason=f"Turtle Entry (high {bar_high:.2f} > {self.prev_entry_high:.2f})",
                )

        self.prev_entry_high = entry_high
        self.prev_exit_low = exit_low
