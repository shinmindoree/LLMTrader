# AGENTS.md — LLMTrader Central Control

## Project Context

**Business Goal:** Binance USDT-M Futures automated trading system (testnet/mainnet).

**Tech Stack:**
- Python 3.11+, uv (package manager)
- FastAPI (optional API server)
- Streamlit (monitoring UI)
- httpx (async HTTP), Pydantic (validation)
- Binance Futures REST API (HMAC-SHA256 signing)

**Current Scope:** Live trading only. LLM strategy generation, backtesting, and paper trading modules have been removed.

---

## Operational Commands

```bash
# Install dependencies
uv sync --extra dev

# Run live trading (primary use case)
uv run python scripts/run_live_trading.py <strategy_file.py> \
  --symbol BTCUSDT \
  --leverage 5 \
  --max-position 1.0 \
  --daily-loss-limit 500 \
  --yes

# Smoke test (verify order execution without running strategy)
uv run python scripts/smoke_live_constraints.py \
  --symbol BTCUSDT --leverage 5 --max-position 1.0 --fraction 0.05

# Check real-time RSI
uv run python scripts/check_realtime_btcusdt_rsi.py --watch

# Run Streamlit UI
uv run streamlit run streamlit_app.py

# Run tests
uv run pytest

# Lint & format
uv run ruff check src tests scripts
uv run black src tests scripts
```

---

## Golden Rules

### Immutable Constraints

1. **Testnet First:** Always verify on `https://testnet.binancefuture.com` before mainnet.
2. **Secrets in .env:** Never hardcode API keys. Use `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `SLACK_WEBHOOK_URL`, `APPLICATIONINSIGHTS_CONNECTION_STRING`.
3. **ReduceOnly for Exits:** Position closing orders must use `reduceOnly=True` to prevent accidental position increase.
4. **Decimal Precision:** Use `decimal.Decimal` for quantity calculations to avoid Binance `-1111` precision errors.

### Do's

- Use `rsi_wilder_from_closes()` for RSI (Wilder/RMA method matches TradingView/Binance).
- Send quantity as string in API calls to preserve precision.
- Bypass risk manager validation for `reduceOnly` orders (allow liquidation always).
- Include `--yes` flag for non-interactive container/CI environments.
- Log real-time price, bar timestamp, RSI values in live trading snapshots.

### Don'ts

- Do not use EMA-based RSI (causes mismatch with exchange charts).
- Do not block position exits due to order size limits.
- Do not use `float` for quantity stepping—use `Decimal.quantize(ROUND_DOWN)`.
- Do not run live trading without setting leverage first via API.
- Do not assume `klines[-1]` is closed—check `closeTime` vs current time.

---

## Standards & References

### Coding Conventions
- Style: `ruff` + `black` (line length 100)
- Type hints required (`mypy --strict` compatible)
- Async/await for all Binance API calls
- Pydantic models for request/response schemas

### Git Strategy
- Branch: `main` (production), feature branches for development
- Commit format: `<type>: <description>` (e.g., `fix: handle precision in order qty`)
- CI: GitHub Actions auto-deploys to Azure Container Apps on push to `main`

### Maintenance Policy
When rules and code diverge, propose an update to this file. Self-healing is expected.

---

## Context Map (Action-Based Routing)

- **[Live Trading Engine](./src/llmtrader/live/AGENTS.md)** — Core trading logic: context, engine, risk management, price feed.
- **[Binance API Client](./src/llmtrader/binance/AGENTS.md)** — REST API integration, HMAC signing, order placement.
- **[CLI Scripts](./scripts/AGENTS.md)** — Entry points for live trading, smoke tests, RSI monitoring.
- **[Strategy Interface](./src/llmtrader/strategy/AGENTS.md)** — Base class and context protocol for custom strategies.
- **[Logging (Azure)](./src/llmtrader/logging/AGENTS.md)** — Structured logging, Application Insights, error alerts.

---

## File Structure (Post-Cleanup)

```
LLMTrader/
├── src/llmtrader/
│   ├── live/           # Live trading engine (core)
│   ├── binance/        # Binance API client
│   ├── strategy/       # Strategy base class
│   ├── indicators/     # RSI (Wilder method)
│   ├── notifications/  # Slack webhooks
│   ├── logging/        # Azure Application Insights (NEW)
│   ├── api/            # FastAPI routers (optional)
│   └── settings.py     # Environment configuration
├── scripts/            # CLI entry points
├── pages/              # Streamlit UI (live only)
├── tests/              # Pytest suite
├── Dockerfile          # Container deployment
└── .env                # Secrets (gitignored)
```

