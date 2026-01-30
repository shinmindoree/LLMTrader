python
import math
from strategy.base import Strategy
from strategy.context import StrategyContext


def _last_non_nan(values):
    for v in reversed(values):
        if v is not None and math.isfinite(v):
            return v
    return None


def register_talib_indicator_all_outputs(ctx: StrategyContext, indicator_name: str):
    """
    Register a TA-Lib indicator with all outputs using the configuration
    stored in the strategy's indicator_config.
    """
    config = ctx.strategy.indicator_config.get(indicator_name, {})
    ctx.register_indicator(indicator_name, **config)


class RsiMeanReversionStrategy(Strategy):
    INDICATOR_NAME = "RSI"

    def __init__(self):
        super().__init__()
        self.params = {
            "rsi_period": 14,
            "oversold": 30.0,
            "overbought": 70.0,
            "exit_mid": 50.0,
            "quantity": 1.0,
        }
        self.indicator_config = {
            self.INDICATOR_NAME: {
                "timeperiod": self.params["rsi_period"],
            }
        }
        self.prev_value = None
        self.is_closing = False

    def initialize(self, ctx: StrategyContext):
        register_talib_indicator_all_outputs(ctx, self.INDICATOR_NAME)
        self.prev_value = None
        self.is_closing = False

    def on_bar(self, ctx: StrategyContext, bar: dict):
        # 1. Reset closing flag if flat
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
        if value is None or not math.isfinite(value):
            return

        # 5. Initialize previous value
        if self.prev_value is None or not math.isfinite(self.prev_value):
            self.prev_value = value
            return

        # 6. Closing logic
        if ctx.position_size > 0 and not self.is_closing:
            if value >= self.params["exit_mid"]:
                self.is_closing = True
                ctx.close_position(quantity=abs(ctx.position_size))
                self.prev_value = value
                return

        if ctx.position_size < 0 and not self.is_closing:
            if value <= self.params["exit_mid"]:
                self.is_closing = True
                ctx.close_position(quantity=abs(ctx.position_size))
                self.prev_value = value
                return

        # 7. Entry logic
        if ctx.position_size == 0:
            if value <= self.params["oversold"]:
                ctx.enter_long(quantity=self.params["quantity"])
            elif value >= self.params["overbought"]:
                ctx.enter_short(quantity=self.params["quantity"])

        # 8. Update previous value
        self.prev_value = value