# AGENTS.md — Logging Module (Azure Application Insights)

## Module Context

Structured logging with Azure Application Insights integration for real-time error detection and long-term analysis.

**Files:**
- `azure_logger.py` — AzureLogger class with trading-specific log methods

**Dependencies:**
- `azure-monitor-opentelemetry` (optional, install with `uv sync --extra azure`)
- Standard Python `logging` module

---

## Features

1. **Dual Output:** Console (immediate visibility) + Azure (persistent, queryable)
2. **Structured Events:** Typed methods for TICK, ORDER, ERROR, SIGNAL, RISK_EVENT
3. **Auto Alerts:** Azure Monitor can trigger alerts on ERROR/CRITICAL logs
4. **Session Tracking:** SESSION_START/SESSION_END for trade session analysis

---

## Setup

### 1. Install Azure SDK (Optional)

```bash
uv add azure-monitor-opentelemetry
# or
uv sync --extra azure
```

### 2. Configure Connection String

In `.env`:
```bash
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=xxx;IngestionEndpoint=https://xxx.in.applicationinsights.azure.com/
```

Get the connection string from Azure Portal:
- Application Insights resource -> Overview -> Connection String

### 3. Create Application Insights Resource

```bash
az monitor app-insights component create \
  --app llmtrader-insights \
  --location southeastasia \
  --resource-group autotrader \
  --application-type web
```

---

## Log Event Types

| Event | Level | Purpose | Azure Query |
|-------|-------|---------|-------------|
| TICK | INFO | 1-second price/RSI snapshot | `traces \| where message == "TICK"` |
| ORDER | WARNING | Trade entry/exit | `traces \| where message == "ORDER"` |
| TRADE_ERROR | ERROR | Exceptions (triggers alerts) | `traces \| where severityLevel >= 3` |
| SIGNAL | INFO | Strategy signal detection | `traces \| where message == "SIGNAL"` |
| RISK_EVENT | WARNING | Risk limit hit | `traces \| where message == "RISK_EVENT"` |
| SESSION_START | WARNING | Trading session start | `traces \| where message == "SESSION_START"` |
| SESSION_END | WARNING | Trading session end | `traces \| where message == "SESSION_END"` |

---

## Azure Alert Configuration

### Create Alert Rule for Errors

1. Go to Application Insights -> Alerts -> Create alert rule
2. Condition: Custom log search

```kusto
traces
| where severityLevel >= 3
| where message == "TRADE_ERROR"
| project timestamp, customDimensions.error_type, customDimensions.error_message
```

3. Action: Send to Slack webhook or email
4. Alert logic: Whenever count > 0 in 5 minute window

---

## Usage in Code

```python
from llmtrader.logging import get_logger

logger = get_logger("llmtrader.live")

# Tick logging (every second)
logger.log_tick(
    symbol="BTCUSDT",
    bar_time="2024-12-21T10:30",
    price=98000.0,
    rsi=45.2,
    rsi_rt=44.8,
    position=0.01,
    balance=5000.0,
    pnl=50.0,
)

# Order logging
logger.log_order(
    event="ENTRY",
    symbol="BTCUSDT",
    side="BUY",
    qty=0.01,
    price=98000.0,
    order_id="12345",
    rsi=30.5,
)

# Error logging (triggers Azure alert)
logger.log_error(
    error_type="ORDER_FAILED",
    message="Insufficient margin",
    symbol="BTCUSDT",
)
```

---

## Local Golden Rules

### Do's

- Use `log_error()` for any exception that should trigger investigation.
- Include `symbol` in all log calls for filtering.
- Log SESSION_END even on abnormal termination (use try-finally).

### Don'ts

- Do not log sensitive data (API keys, secrets).
- Do not use `print()` directly—use logger for consistent formatting.
- Do not skip Azure setup verification before production deployment.

