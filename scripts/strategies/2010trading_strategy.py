python
from strategy.base import Strategy
from strategy.context import StrategyContext
import math


def _last_non_nan(values):
    if values is None:
        return None
    for v in reversed(values):
        if v is not None and math.isfinite(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, indicator_name: str):
    ctx.register_indicator(indicator_name)


def crossed_above(prev_a, prev_b, a, b):
    return prev_a <= prev_b and a > b


def crossed_below(prev_a, prev_b, a, b):
    return prev_a >= prev_b and a < b


class MaBreakoutLongStrategy(Strategy):
    INDICATOR_NAME = "SMA"

    def __init__(self):
        self.params = {
            "entry_period": 20,
            "exit_period": 10,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "periods": [self.params["entry_period"], self.params["exit_period"]]
            }
        }
        self.prev_ma20 = None
        self.prev_ma10 = None
        self.prev_price = None
        self.is_closing = False

    def initialize(self, ctx: StrategyContext):
        register_talib_indicator_all_outputs(ctx, self.INDICATOR_NAME)
        self.prev_ma20 = None
        self.prev_ma10 = None
        self.prev_price = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict):
        # 1. Reset closing flag if flat
        if ctx.position_size == 0:
            self.is_closing = False

        # 2. Skip if there are open orders
        if ctx.get_open_orders():
            return

        # 3. Only process on new bar
        if not bar.get("is_new_bar", True):
            return

        price = bar.get("close")
        if price is None or not math.isfinite(price):
            return

        # 4. Get indicator values
        ma20 = ctx.get_indicator(self.INDICATOR_NAME, period=self.params["entry_period"])
        ma10 = ctx.get_indicator(self.INDICATOR_NAME, period=self.params["exit_period"])

        if not (math.isfinite(ma20) and math.isfinite(ma10)):
            return

        # 5. Initialize previous values
        if (
            self.prev_ma20 is None
            or self.prev_ma10 is None
            or self.prev_price is None
            or not (
                math.isfinite(self.prev_ma20)
                and math.isfinite(self.prev_ma10)
                and math.isfinite(self.prev_price)
            )
        ):
            self.prev_ma20 = ma20
            self.prev_ma10 = ma10
            self.prev_price = price
            return

        # 6. Closing logic (long only)
        if (
            ctx.position_size > 0
            and not self.is_closing
            and crossed_below(self.prev_price, self.prev_ma10, price, ma10)
        ):
            self.is_closing = True
            ctx.close_position()
            self.prev_ma20 = ma20
            self.prev_ma10 = ma10
            self.prev_price = price
            return

        # 7. Entry logic (long only)
        if (
            ctx.position_size == 0
            and crossed_above(self.prev_price, self.prev_ma20, price, ma20)
        ):
            ctx.enter_long()

        # 8. Update previous values
        self.prev_ma20 = ma20
        self.prev_ma10 = ma10
        self.prev_price = price