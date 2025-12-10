"""단순 이동평균 크로스오버 전략 예시."""

from typing import Any

from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext


class SimpleMAStrategy(Strategy):
    """단순 이동평균 크로스오버 전략.

    짧은 이동평균이 긴 이동평균을 상향 돌파하면 매수,
    하향 돌파하면 매도.
    """

    def __init__(self, fast_period: int = 10, slow_period: int = 30, quantity: float = 0.01) -> None:
        """전략 초기화.

        Args:
            fast_period: 빠른 이동평균 기간
            slow_period: 느린 이동평균 기간
            quantity: 주문 수량
        """
        super().__init__()
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.quantity = quantity
        self.prev_fast_ma: float | None = None
        self.prev_slow_ma: float | None = None

    def initialize(self, ctx: StrategyContext) -> None:
        """전략 초기화."""
        self.prev_fast_ma = None
        self.prev_slow_ma = None

    def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
        """새 캔들 도착 시 매매 로직 실행."""
        # 이동평균 계산 (임시: ctx.get_indicator 가정)
        fast_ma = ctx.get_indicator("sma", self.fast_period)
        slow_ma = ctx.get_indicator("sma", self.slow_period)

        # 초기화 대기
        if self.prev_fast_ma is None or self.prev_slow_ma is None:
            self.prev_fast_ma = fast_ma
            self.prev_slow_ma = slow_ma
            return

        # 골든 크로스 (상향 돌파) → 매수
        if self.prev_fast_ma <= self.prev_slow_ma and fast_ma > slow_ma:
            if ctx.position_size <= 0:
                ctx.buy(self.quantity)

        # 데드 크로스 (하향 돌파) → 매도
        elif self.prev_fast_ma >= self.prev_slow_ma and fast_ma < slow_ma:
            if ctx.position_size > 0:
                ctx.close_position()

        self.prev_fast_ma = fast_ma
        self.prev_slow_ma = slow_ma




