python
import math
import random
from strategy.base import Strategy
from strategy.context import StrategyContext


def _last_non_nan(values):
    if values is None:
        return None
    for v in reversed(values):
        if v is not None and isinstance(v, (int, float)) and math.isfinite(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, indicator_name: str):
    """
    Register a TA-Lib indicator and all of its outputs with default settings.
    """
    ctx.register_indicator(indicator_name)


class RandomEntryTakeProfitStrategy(Strategy):
    """
    Randomly enters a position at any time.
    Exits immediately when profit reaches +0.5%.
    """

    INDICATOR_NAME = "RSI"

    def __init__(self):
        self.params = {
            "rsi_period": 14,
            "entry_probability": 0.05,
            "take_profit": 0.005,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "period": self.params["rsi_period"],
            }
        }
        self.prev_value = None
        self.is_closing = False

    def initialize(self, ctx: StrategyContext):
        register_talib_indicator_all_outputs(ctx, self.INDICATOR_NAME)
        self.prev_value = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict):
        # 1. Reset closing state if flat
        if ctx.position_size == 0:
            self.is_closing = False

        # 2. Skip if there are open orders
        if ctx.get_open_orders():
            return

        # 3. Only act on new bars
        if not bar.get("is_new_bar", True):
            return

        # 4. Get indicator value
        value = ctx.get_indicator(
            self.INDICATOR_NAME,
            period=self.params["rsi_period"],
        )
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return

        # 5. Initialize previous value
        if self.prev_value is None or not math.isfinite(self.prev_value):
            self.prev_value = value
            return

        price = bar.get("close")

        # 6. Closing logic
        if ctx.position_size > 0 and not self.is_closing:
            entry_price = ctx.position_avg_price
            if entry_price and (price - entry_price) / entry_price >= self.params["take_profit"]:
                self.is_closing = True
                ctx.close_position()
                self.prev_value = value
                return

        if ctx.position_size < 0 and not self.is_closing:
            entry_price = ctx.position_avg_price
            if entry_price and (entry_price - price) / entry_price >= self.params["take_profit"]:
                self.is_closing = True
                ctx.close_position()
                self.prev_value = value
                return

        # 7. Entry logic (random)
        if ctx.position_size == 0:
            if random.random() < self.params["entry_probability"]:
                if random.random() < 0.5:
                    ctx.enter_long()
                else:
                    ctx.enter_short()

        # 8. Update previous value
        self.prev_value = value