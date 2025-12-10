from llmtrader.strategy.base import Strategy
from llmtrader.strategy.context import StrategyContext


class MovingAverageCrossoverStrategy(Strategy):
    def __init__(self, short_period: int = 10, long_period: int = 30):
        super().__init__()
        # Initialize parameters for short and long moving averages
        self.short_period = short_period
        self.long_period = long_period
        self.short_ma = None
        self.long_ma = None
        self.previous_short_ma = None
        self.previous_long_ma = None

    def initialize(self, ctx: StrategyContext) -> None:
        # Initialize moving averages at strategy start
        self.short_ma = ctx.get_indicator("sma", self.short_period)
        self.long_ma = ctx.get_indicator("sma", self.long_period)
        self.previous_short_ma = self.short_ma
        self.previous_long_ma = self.long_ma

    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        # Update moving averages
        self.short_ma = ctx.get_indicator("sma", self.short_period)
        self.long_ma = ctx.get_indicator("sma", self.long_period)

        # Check for moving average crossover
        if (
            self.previous_short_ma < self.previous_long_ma
            and self.short_ma > self.long_ma
        ):
            # If the short moving average crosses above the long moving average, buy
            ctx.buy(0.01)  # Buy 0.01 BTC (~$920)
        elif (
            self.previous_short_ma > self.previous_long_ma
            and self.short_ma < self.long_ma
        ):
            # If the short moving average crosses below the long moving average, sell
            ctx.sell(0.01)  # Sell 0.01 BTC

        # Update previous moving averages
        self.previous_short_ma = self.short_ma
        self.previous_long_ma = self.long_ma
