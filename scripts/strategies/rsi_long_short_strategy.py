import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext


def crossed_above(prev: float, current: float, level: float) -> bool:
    """prev < level <= current"""
    return prev < level <= current


def crossed_below(prev: float, current: float, level: float) -> bool:
    """current <= level < prev"""
    return current <= level < prev


class RsiLongShortStrategy(Strategy):
    """RSI 기반 롱/숏 전략.

    목적:
    - RSI 지표를 활용한 양방향 트레이딩 전략

    규칙:
    - 롱 포지션 진입: RSI(기본 14)가 long_entry_rsi 아래에서 long_entry_rsi 상향 돌파 시 진입
    - 롱 포지션 청산: RSI가 long_exit_rsi 상향 돌파 시 청산
    - 숏 포지션 진입: RSI가 short_entry_rsi 위에서 short_entry_rsi 하향 돌파 시 진입
    - 숏 포지션 청산: RSI가 short_exit_rsi 하향 돌파 시 청산

    참고:
    - StopLoss/수량 산정은 시스템(Context/Risk)에서 처리
    - 새 봉(is_new_bar=True)에서만 RSI 크로스 판단/prev_rsi 갱신
    - 롱과 숏 포지션은 동시에 존재할 수 없음 (position_size로 관리)
    """

    def __init__(
        self,
        rsi_period: int = 14,
        long_entry_rsi: float = 30.0,
        long_exit_rsi: float = 70.0,
        short_entry_rsi: float = 70.0,
        short_exit_rsi: float = 30.0,
    ) -> None:
        super().__init__()
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
        self.prev_rsi: float | None = None
        self.is_closing: bool = False  # 청산 주문 진행 중 플래그 (중복 청산 방지)
        self.indicator_config = {
            "RSI": {"period": self.rsi_period},
        }

    def initialize(self, ctx: StrategyContext) -> None:
        print(f"🚀 [버전확인] RsiLongShortStrategy v1.0 시작!")
        self.prev_rsi = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        # ===== 청산 플래그 리셋 =====
        if ctx.position_size == 0:
            self.is_closing = False

        # ===== 미체결 주문 가드 =====
        open_orders = ctx.get_open_orders()
        if open_orders:
            return

        # RSI는 "마지막 닫힌 봉 close" 기준이어야 하므로,
        # 새 봉이 확정된 시점(is_new_bar=True)에서만 크로스 판단/prev_rsi 갱신.
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
                reason_msg = f"RSI Exit Long ({self.prev_rsi:.1f} -> {rsi:.1f})"
                ctx.close_position(reason=reason_msg)
                self.prev_rsi = rsi
                return

        # ===== 숏 포지션 청산: RSI short_exit_rsi 하향 돌파 =====
        if ctx.position_size < 0 and not self.is_closing:
            if crossed_below(self.prev_rsi, rsi, self.short_exit_rsi):
                self.is_closing = True
                reason_msg = f"RSI Exit Short ({self.prev_rsi:.1f} -> {rsi:.1f})"
                ctx.close_position(reason=reason_msg)
                self.prev_rsi = rsi
                return

        # ===== 롱 진입: RSI long_entry_rsi 상향 돌파 =====
        if ctx.position_size == 0:
            if crossed_above(self.prev_rsi, rsi, self.long_entry_rsi):
                reason_msg = f"Entry Long ({self.prev_rsi:.1f} -> {rsi:.1f})"
                ctx.enter_long(reason=reason_msg)

        # ===== 숏 진입: RSI short_entry_rsi 하향 돌파 =====
        if ctx.position_size == 0:
            if crossed_below(self.prev_rsi, rsi, self.short_entry_rsi):
                reason_msg = f"Entry Short ({self.prev_rsi:.1f} -> {rsi:.1f})"
                ctx.enter_short(reason=reason_msg)

        self.prev_rsi = rsi
