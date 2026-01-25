# AGENTS.md — CLI Scripts

## Module Context

Entry points for running live trading and diagnostic utilities.

**Files:**
- `run_live_trading.py` — Main live trading script with CLI args
- `smoke_live_constraints.py` — Quick order test without strategy
- `check_realtime_btcusdt_rsi.py` — RSI monitoring utility
- `min_order_test.py` — Minimal order placement test

---

## Implementation Patterns

### Argument Parsing

Use `typer` for CLI:

```python
import typer
app = typer.Typer()

@app.command()
def main(
    strategy_file: str,
    symbol: str = "BTCUSDT",
    leverage: int = 1,
    max_position: float = 0.5,
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
):
    ...
```

### Non-Interactive Mode

Always include `--yes` for container/CI:

```python
if not yes:
    confirm = input("Continue? (yes/no): ")
    if confirm.lower() != "yes":
        raise typer.Exit(1)
```

### Dynamic Strategy Loading

```python
import importlib.util

spec = importlib.util.spec_from_file_location("strategy", strategy_file)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
strategy_class = next(
    cls for cls in vars(module).values()
    if isinstance(cls, type) and issubclass(cls, Strategy) and cls is not Strategy
)
```

---

## Script Reference

### run_live_trading.py

```bash
uv run python scripts/run_live_trading.py rsi_long_short_strategy.py \
  --streams '[{"symbol":"BTCUSDT","interval":"1m","leverage":5,"max_position":1.0,"daily_loss_limit":500,"max_consecutive_losses":0,"stop_loss_pct":0.05,"stoploss_cooldown_candles":0}]' \
  --yes
```

### smoke_live_constraints.py

```bash
uv run python scripts/smoke_live_constraints.py \
  --symbol BTCUSDT \
  --leverage 5 \
  --max-position 1.0 \
  --fraction 0.05
```

### check_realtime_btcusdt_rsi.py

```bash
uv run python scripts/check_realtime_btcusdt_rsi.py --watch
```

---

## Local Golden Rules

### Do's

- Set `max_order_size = max_position_size` in RiskConfig to allow full entry.
- Use `Decimal` for quantity calculation in smoke tests.
- Print summary statistics on script exit (use `try-finally`).

### Don'ts

- Do not prompt for input in Docker—always support `--yes`.
- Do not hardcode symbol or leverage—use CLI args.
