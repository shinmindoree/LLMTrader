# AGENTS.md — Live Trading Module

## Module Context

This is the **core business logic** module for live trading execution on Binance Futures.

**Dependencies:**
- `llmtrader.binance.client` — HTTP client for API calls
- `llmtrader.strategy.base` — Strategy interface
- `llmtrader.indicators.rsi` — Wilder RSI calculation
- `llmtrader.notifications.slack` — Order notifications

**Files:**
- `context.py` — LiveContext: position/balance management, order execution
- `engine.py` — LiveTradingEngine: main loop, price feed subscription, strategy dispatch
- `price_feed.py` — PriceFeed: REST polling for real-time prices and closed bars
- `risk.py` — RiskConfig/RiskManager: order size, position limits, daily loss

---

## Tech Stack & Constraints

- **Async-only:** All methods interacting with Binance must be `async`.
- **No external state:** Position/balance fetched from exchange on each cycle.
- **RSI Method:** Use `rsi_wilder_from_closes()` exclusively.

---

## Implementation Patterns

### Price Feed Closed-Bar Detection

```python
# Determine if a kline is "closed" using closeTime
safe_ts = recv_ts - 1500  # 1.5s buffer for network delay
closed = [k for k in klines if k[6] <= safe_ts]
bar = closed[-1] if closed else klines[-2]
```

### Order Quantity with Decimal Precision

```python
from decimal import Decimal, ROUND_DOWN

qty_step = Decimal("0.001")
raw_qty = Decimal(str(notional / price))
stepped_qty = (raw_qty / qty_step).to_integral_value(ROUND_DOWN) * qty_step
quantity = str(stepped_qty)  # Send as string to API
```

### ReduceOnly Bypass in Risk Validation

```python
# In _place_order():
if not reduce_only:
    if not self.risk_manager.validate_order_size(...):
        return None
# ReduceOnly orders skip validation to ensure exits always work
```

### Order Inflight Lock with Timeout

```python
if self._order_inflight:
    if (now - self._order_inflight_since) > 5.0:
        self._order_inflight = False  # Auto-release stuck lock
    else:
        return None  # Skip duplicate order
```

---

## Testing Strategy

```bash
# Unit test risk manager
uv run pytest tests/ -k "risk"

# Integration: smoke test live order flow
uv run python scripts/smoke_live_constraints.py --symbol BTCUSDT --leverage 5
```

---

## Local Golden Rules

### Do's

- Always fetch `walletBalance` (not `availableBalance`) for equity calculation.
- Include `last` (real-time price) and `rsi_rt` in order logs.
- Calculate estimated PnL on EXIT and include in Slack notification.
- Use `is_new_bar` flag to separate bar-based signals from tick-based stop-loss.

### Don'ts

- Do not use `position_entry_price` from local state—re-fetch from exchange if critical.
- Do not assume `ctx.balance` equals available margin (it's wallet balance).
- Do not skip seeding `price_history` at startup (RSI will be 50.0 otherwise).
- Do not call `strategy.on_bar()` on every tick unless `strategy.run_on_tick = True`.

