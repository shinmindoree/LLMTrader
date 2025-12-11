from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext


class RsiBreakoutStrategy(Strategy):
    def __init__(self, rsi_low: int = 30, rsi_high: int = 70, quantity: float = 0.01):
        super().__init__()
        # Validate parameters
        assert 0 <= rsi_low <= 100, "Invalid rsi_low"
        assert 0 <= rsi_high <= 100, "Invalid rsi_high"
        assert rsi_low < rsi_high, "rsi_low should be less than rsi_high"
        assert quantity > 0, "quantity should be greater than 0"

        # Initialize parameters
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.quantity = quantity

        # Initialize state
        self.prev_rsi = None

    def initialize(self, ctx: StrategyContext):
        # Get initial RSI
        self.prev_rsi = ctx.get_indicator("rsi", 14)

    def on_bar(self, ctx: StrategyContext, bar: dict):
        # Get current RSI
        current_rsi = ctx.get_indicator("rsi", 14)

        # Check for buy signal: RSI crosses above rsi_low
        if self.prev_rsi < self.rsi_low and current_rsi > self.rsi_low:
            ctx.buy(self.quantity)

        # Check for sell signal: RSI crosses below rsi_high
        elif self.prev_rsi > self.rsi_high and current_rsi < self.rsi_high:
            ctx.sell(self.quantity)

        # Update previous RSI
        self.prev_rsi = current_rsi
