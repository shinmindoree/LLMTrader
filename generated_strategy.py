from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext


class RsiBreakoutStrategy(Strategy):
    def __init__(self, rsi_low: int = 30, rsi_high: int = 70, quantity: float = 0.01):
        super().__init__()
        if not 0 <= rsi_low < rsi_high <= 100:
            raise ValueError("Invalid RSI thresholds")
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.quantity = quantity

    def initialize(self, ctx: StrategyContext) -> None:
        # Initialize the previous RSI value to None
        self.prev_rsi = None

    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        # Calculate the current RSI
        current_rsi = ctx.get_indicator("rsi", 14)

        # If the previous RSI value is not None, we can check for RSI breakout
        if self.prev_rsi:
            # If the previous RSI is below the low threshold and the current RSI is above it, buy
            if self.prev_rsi < self.rsi_low and current_rsi > self.rsi_low:
                ctx.buy(self.quantity)

            # If the previous RSI is above the high threshold and the current RSI is below it, sell
            elif self.prev_rsi > self.rsi_high and current_rsi < self.rsi_high:
                ctx.sell(self.quantity)

        # Update the previous RSI value
        self.prev_rsi = current_rsi
