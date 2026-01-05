# AGENTS.md — Strategy Interface

## Module Context

Defines the base class and context protocol for trading strategies.

**Files:**
- `base.py` — Strategy base class with lifecycle methods
- `context.py` — StrategyContext protocol for order execution and indicators

---

## Strategy Lifecycle

```python
class Strategy:
    def initialize(self, ctx: StrategyContext) -> None:
        """Called once at startup."""
        pass

    def on_bar(self, ctx: StrategyContext, bar: dict) -> None:
        """Called on each price update (bar or tick)."""
        pass

    def finalize(self, ctx: StrategyContext) -> None:
        """Called on shutdown."""
        pass
```

---

## Bar Dictionary Structure

```python
bar = {
    "timestamp": 1234567890000,      # Local receive time (ms)
    "bar_timestamp": 1234567800000,  # Closed bar open time (ms)
    "bar_close": 88000.0,            # Closed bar close price
    "price": 88050.0,                # Real-time price (for stop-loss)
    "is_new_bar": True,              # True if bar_timestamp changed
    "volume": 100.5,
}
```

---

## Context Methods

```python
class StrategyContext(Protocol):
    @property
    def current_price(self) -> float: ...
    
    @property
    def position_size(self) -> float: ...
    
    @property
    def position_entry_price(self) -> float: ...
    
    def buy(self, quantity: float) -> None: ...
    
    def sell(self, quantity: float) -> None: ...
    
    def close_position(self) -> None: ...
    
    def get_indicator(self, name: str, period: int) -> float: ...
```

**Supported Indicators:**
- `"rsi"` — Wilder RSI from closed bar prices
- `"rsi_rt"` — Real-time RSI (includes current tick price)
- `"sma"` — Simple moving average
- `"ema"` — Exponential moving average

---

## Implementation Patterns

### RSI Crossover Detection

```python
def on_bar(self, ctx, bar):
    rsi = ctx.get_indicator("rsi", 14)
    
    if self.prev_rsi is not None:
        # Entry: RSI crosses above 30
        if self.prev_rsi < 30 <= rsi and ctx.position_size == 0:
            ctx.buy(qty)
        
        # Exit: RSI crosses above 70
        if self.prev_rsi < 70 <= rsi and ctx.position_size > 0:
            ctx.close_position()
    
    if bar.get("is_new_bar"):
        self.prev_rsi = rsi
```

### Tick-Based Stop-Loss

```python
class MyStrategy(Strategy):
    run_on_tick = True  # Engine calls on_bar every tick
    
    def on_bar(self, ctx, bar):
        # Stop-loss uses real-time price
        if ctx.position_size > 0:
            entry = ctx.position_entry_price
            if ctx.current_price <= entry - self.stop_loss:
                ctx.close_position()
                return
        
        # RSI logic only on new bar
        if bar.get("is_new_bar"):
            self._check_rsi_signals(ctx, bar)
```

### Automatic Position Sizing

```python
from decimal import Decimal, ROUND_DOWN

def on_bar(self, ctx, bar):
    if ctx.position_size == 0 and should_enter:
        equity = ctx.balance + ctx.unrealized_pnl
        notional = equity * self.leverage * self.max_position * 0.98
        raw_qty = Decimal(str(notional / ctx.current_price))
        qty = float((raw_qty / self.qty_step).to_integral_value(ROUND_DOWN) * self.qty_step)
        if qty >= self.min_qty:
            ctx.buy(qty)
```

---

## Local Golden Rules

### Do's

- Check `ctx.position_size == 0` before BUY (long-only enforcement).
- Check `ctx.position_size > 0` before SELL/close (prevent invalid exits).
- Update `prev_rsi` only on `is_new_bar=True` to avoid false crossovers.

### Don'ts

- Do not use `ctx.get_indicator("rsi", period)` with current tick price—use `"rsi_rt"`.
- Do not assume `on_bar` is called exactly once per minute—tick mode may call more often.

