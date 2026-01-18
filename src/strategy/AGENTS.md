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
    
    def buy(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
    ) -> None: ...
    
    def sell(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
    ) -> None: ...
    
    def close_position(
        self,
        reason: str | None = None,
        exit_reason: str | None = None,
    ) -> None: ...

    def calc_entry_quantity(self, entry_pct: float | None = None, price: float | None = None) -> float: ...
    
    def enter_long(self, reason: str | None = None, entry_pct: float | None = None) -> None: ...
    
    def enter_short(self, reason: str | None = None, entry_pct: float | None = None) -> None: ...
    
    def get_indicator(self, name: str, period: int) -> float: ...

    def register_indicator(self, name: str, func: Callable[..., Any]) -> None: ...
```

**Indicators (builtin + custom):**
- builtin 지표는 TA-Lib 함수명으로 바로 호출한다. 예: `ctx.get_indicator("RSI", period=14)`
- custom 지표는 전략에서 `initialize()` 시점에 `ctx.register_indicator(name, func)`로 등록한다.

---

## Implementation Patterns

### RSI Crossover Detection

```python
def initialize(self, ctx):
    pass

def on_bar(self, ctx, bar):
    rsi = ctx.get_indicator("RSI", period=14)
    
    if self.prev_rsi is not None:
        # Entry: RSI crosses above 30
        if self.prev_rsi < 30 <= rsi and ctx.position_size == 0:
            ctx.enter_long(reason=f"RSI cross ({self.prev_rsi:.1f} -> {rsi:.1f})")
        
        # Exit: RSI crosses above 70
        if self.prev_rsi < 70 <= rsi and ctx.position_size > 0:
            ctx.close_position()
    
    if bar.get("is_new_bar"):
        self.prev_rsi = rsi
```

### StopLoss Handling

StopLoss는 시스템(Context/Risk)에서 처리합니다. 전략은 진입/청산 신호만 관리하고,
StopLoss 트리거는 시스템 설정으로 통일합니다.

### Automatic Position Sizing

```python
def on_bar(self, ctx, bar):
    if ctx.position_size == 0 and should_enter:
        ctx.enter_long(reason="entry signal")
```

---

## Local Golden Rules

### Do's

- Check `ctx.position_size == 0` before BUY (long-only enforcement).
- Check `ctx.position_size > 0` before SELL/close (prevent invalid exits).
- Update `prev_rsi` only on `is_new_bar=True` to avoid false crossovers.

### Don'ts

- Do not assume indicators are tick-based; indicator values are calculated from closed-bar history.
- Do not assume `on_bar` is called exactly once per minute—tick mode may call more often.
