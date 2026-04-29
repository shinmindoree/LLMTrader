# OI Ingestor — Container Apps Deployment

The **OI Ingestor** is a small, single-replica long-running worker that polls
Binance USDM perpetual `openInterestHist?period=5m` every 5 minutes and writes
the values to a Redis sorted set (`oi:{SYMBOL}:hist`).

It is the *only* live data dependency of the **OI Capitulation-Bottom**
strategy (`scripts/strategies/oi_capitulation_bottom_strategy.py`). The runner
(live trading container) reads OI from Redis via `src/indicators/oi_provider.py`.

## Why a separate Container App?

The runner is event-loop-driven (per-symbol asyncio task). The OI ingestor has
a *different* lifecycle: a 5-minute cron, plus a single-replica invariant
(double-writes are tolerated but wasteful). Running it next to the runner
would mix:
- a high-frequency trading hot-path
- a low-frequency, blocking, network-bound poller

Splitting prevents the poller's Binance latency spikes from interfering with
the trader's tick processing.

## Architecture

```
[Binance fapi]
     │ /futures/data/openInterestHist?period=5m
     ▼
[OI Ingestor Container App]   <-- this doc
     │ ZADD  oi:BTCUSDT:hist  "{ts}:{sum_oi}"  score=ts
     │ ZREMRANGEBYSCORE oi:BTCUSDT:hist -inf (now-30h)
     ▼
[Azure Cache for Redis] ──────► [Runner Container App] ───► Binance order API
                                       ▲
                                       │ ZREVRANGEBYSCORE oi:BTCUSDT:hist <ts> -inf LIMIT 0 1
                                       │   for now and now-24h, then (cur/prev)-1
                                       └── strategy.on_bar
```

## Resources

- **Image**: built from `infra/Dockerfile.oi_ingestor`
- **CPU/Memory**: `0.25 vCPU / 0.5Gi` is plenty (the poll is tiny)
- **Replicas**: `min=1 max=1` (singleton)
- **Restart policy**: `Always` (Container Apps default for long-running workloads)
- **Probes**: none required; the worker logs every 5 min

## Required environment variables

| Variable          | Required | Default                          | Purpose                                                                 |
| ----------------- | -------- | -------------------------------- | ----------------------------------------------------------------------- |
| `REDIS_URL`       | yes      | —                                | e.g. `rediss://:<key>@<name>.redis.cache.windows.net:6380/0`            |
| `OI_SYMBOLS`      | no       | `BTCUSDT`                        | comma-separated, e.g. `BTCUSDT,ETHUSDT`                                 |
| `OI_POLL_SECONDS` | no       | `300`                            | poll interval                                                           |
| `OI_TRIM_HOURS`   | no       | `30`                             | sorted-set retention                                                    |
| `BINANCE_FAPI`    | no       | `https://fapi.binance.com`       | override for proxy/SaaS access                                          |
| `LOG_LEVEL`       | no       | `INFO`                           | python logging level                                                    |

## Build & deploy (Azure CLI)

Replace placeholders with your existing values (`infra/docs/scaling-architecture-guide.md`
already covers ACR/Container Apps env naming).

```bash
ACR=acrllmtrader                  # your ACR name
RG=rg-llmtrader                   # resource group
ENV=cae-llmtrader                 # Container Apps environment
APP=ca-oi-ingestor                # the new app
IMAGE=$ACR.azurecr.io/oi-ingestor:$(git rev-parse --short HEAD)

# 1) build & push
az acr build -r $ACR -f infra/Dockerfile.oi_ingestor -t oi-ingestor:$(git rev-parse --short HEAD) .

# 2) create (or update) Container App
az containerapp create \
  --resource-group $RG \
  --name $APP \
  --environment $ENV \
  --image $IMAGE \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.25 --memory 0.5Gi \
  --secrets redis-url="$REDIS_URL" \
  --env-vars \
      REDIS_URL=secretref:redis-url \
      OI_SYMBOLS=BTCUSDT \
      OI_POLL_SECONDS=300 \
      OI_TRIM_HOURS=30

# 3) update on subsequent commits
az containerapp update --resource-group $RG --name $APP --image $IMAGE
```

## Verification

After the app is `Running`, check Redis:

```bash
redis-cli -u "$REDIS_URL" ZCARD oi:BTCUSDT:hist     # expect ~360 entries within an hour
redis-cli -u "$REDIS_URL" ZREVRANGE oi:BTCUSDT:hist 0 4 WITHSCORES
```

You should see members of the form `"{epoch_ms}:{sum_oi}"` with monotonically
increasing scores.

## Local dev

```powershell
$env:REDIS_URL = "redis://localhost:6379/0"
$env:PYTHONPATH = "$PWD/src"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -u scripts\oi_ingestor.py
```

## Strategy hand-off

Once OI data is flowing into Redis, run live trading with:

```powershell
$env:REDIS_URL = "..."
.\.venv\Scripts\python.exe scripts\run_live_trading.py `
   scripts\strategies\oi_capitulation_bottom_strategy.py `
   --symbol BTCUSDT --candle-interval 15m `
   --stop-loss-pct 0.012
```

For backtest (uses parquet, no Redis needed):

```powershell
$env:OI_PROVIDER_MODE = "backtest"
.\.venv\Scripts\python.exe scripts\run_backtest.py `
   scripts\strategies\oi_capitulation_bottom_strategy.py `
   --symbol BTCUSDT --candle-interval 15m `
   --start-date 2025-04-29 --end-date 2026-04-29 `
   --stop-loss-pct 0 --commission 0.0002 --max-position 1.0
```

> **Important — backtest flags**:
> - `--stop-loss-pct 0` disables the engine-level SL so the strategy's own
>   intrabar SL (`-1.2%`) is the single source of truth. Otherwise both fire.
> - `--max-position 1.0` matches the discovery sweep's full sizing.
> - `--commission 0.0002` matches `scripts/micro_alpha_lib.py::COMMISSION`.
>
> Verified OOS run (2025-04-29 → 2026-04-29):
> +9.3% net, 147 trades, 50.3% win rate. The discovery sweep on the same
> window reported +34.9% with a vectorized fixed-unit simulator (no compounding,
> no per-bar slippage between signal close and fill).
