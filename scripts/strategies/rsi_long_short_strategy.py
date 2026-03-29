import math
from typing import Any

from strategy.base import Strategy
from strategy.context import StrategyContext

# 웹 UI 파라미터 패널이 이 dict를 읽고 AST로 안전하게 갱신합니다.
STRATEGY_PARAMS: dict[str, Any] = {
    "rsi_period": 14,
    "long_entry_rsi": 30.0,
    "long_exit_rsi": 70.0,
    "short_entry_rsi": 70.0,
    "short_exit_rsi": 30.0,
}

STRATEGY_PARAM_SCHEMA: dict[str, Any] = {
    "rsi_period": {
        "type": "integer", "min": 2, "max": 100,
        "label": "RSI 기간",
        "description": "RSI 계산에 사용할 캔들 수입니다. 값이 작을수록 RSI가 민감하게 변하고, 클수록 부드러워집니다.",
        "group": "지표 (Indicator)",
    },
    "long_entry_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "롱 진입 RSI",
        "description": "RSI가 이 값을 상향 돌파하면 롱 진입합니다. 낮을수록 더 깊은 과매도에서만 진입합니다.",
        "group": "진입 (Entry)",
    },
    "long_exit_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "롱 청산 RSI",
        "description": "RSI가 이 값을 상향 돌파하면 롱 포지션을 청산합니다. 높을수록 더 오래 보유합니다.",
        "group": "청산 (Exit)",
    },
    "short_entry_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "숏 진입 RSI",
        "description": "RSI가 이 값을 하향 돌파하면 숏 진입합니다. 높을수록 더 일찍 과매수 구간에서 진입합니다.",
        "group": "진입 (Entry)",
    },
    "short_exit_rsi": {
        "type": "number", "min": 1, "max": 99,
        "label": "숏 청산 RSI",
        "description": "RSI가 이 값을 하향 돌파하면 숏 포지션을 청산합니다. 낮을수록 더 오래 보유합니다.",
        "group": "청산 (Exit)",
    },
}


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
