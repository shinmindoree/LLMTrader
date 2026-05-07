# Perp-Meta Ingestor — Container Apps Deployment

The **Perp-Meta Ingestor** is a long-running worker that publishes three live
indicators to Redis sorted sets, used by the **Multi-Factor Portfolio**
strategy (`scripts/strategies/multi_factor_portfolio_strategy.py`):

| Indicator   | Endpoint                                             | Cadence | Redis key                 |
| ----------- | ---------------------------------------------------- | ------- | ------------------------- |
| `funding`   | `/fapi/v1/fundingRate`                               | 8h      | `funding:{SYMBOL}:hist`   |
| `taker`     | `/futures/data/takerlongshortRatio?period=5m`        | 5m      | `taker:{SYMBOL}:hist`     |
| `lsr`       | `/futures/data/globalLongShortAccountRatio?period=5m`| 5m      | `lsr:{SYMBOL}:hist`       |

All three sorted sets follow the same shape used by `oi_ingestor`:
member = `"{ts_ms}:{value}"`, score = `ts_ms`.

## Why one Container App for all three?

Each indicator is tiny (a single Binance call every 5–30 min). Grouping them
into one process saves a Container App service plan slot and shares the Redis
connection. Funding has its own slower cadence (`MFP_FUNDING_POLL_SECONDS`,
default 30 min) so its idle CPU contribution is negligible.

## Architecture

```
[Binance fapi]
     │  /fapi/v1/fundingRate
     │  /futures/data/takerlongshortRatio
     │  /futures/data/globalLongShortAccountRatio
     ▼
[Perp-Meta Ingestor Container App]    <-- this doc
     │  ZADD funding:BTCUSDT:hist "{ts}:{rate}"
     │  ZADD taker:BTCUSDT:hist   "{ts}:{ratio}"
     │  ZADD lsr:BTCUSDT:hist     "{ts}:{ratio}"
     │  ZREMRANGEBYSCORE … -inf (now-30h)
     ▼
[Azure Cache for Redis] ──► [Runner Container App] ──► Binance order API
                                    ▲
                                    │  ZREVRANGEBYSCORE <key> <ts> -inf LIMIT 0 1
                                    │  ZRANGEBYSCORE   <key> <start> <end>
                                    └─ MFP strategy on_bar()
```

## Resources

- **Image**: built from `infra/Dockerfile.perp_meta_ingestor`
- **CPU/Memory**: `0.25 vCPU / 0.5Gi`
- **Replicas**: `min=1 max=1` (singleton)
- **Probes**: none required; logs every poll cycle

## Required environment variables

| Variable                    | Required | Default                    | Purpose |
| --------------------------- | -------- | -------------------------- | ------- |
| `REDIS_URL` *or* `REDIS_HOST` + (`REDIS_USERNAME` *or* `REDIS_PASSWORD`) | yes | — | Redis auth (URL key auth, or AAD via username, or access-key) |
| `MFP_SYMBOLS`               | no       | `BTCUSDT`                  | comma-separated symbols |
| `MFP_INDICATORS`            | no       | `funding,taker,lsr`        | subset of the three |
| `MFP_POLL_SECONDS`          | no       | `300`                      | taker / lsr cadence |
| `MFP_FUNDING_POLL_SECONDS`  | no       | `1800`                     | funding cadence (8h source) |
| `MFP_TRIM_HOURS`            | no       | `30`                       | ZSET retention |
| `BINANCE_FAPI`              | no       | `https://fapi.binance.com` | proxy/SaaS override |
| `LOG_LEVEL`                 | no       | `INFO`                     | python logging level |

## Build & deploy (Azure CLI)

```bash
ACR=fdpotestacr                          # your ACR name
RG=fdpo-test-rg                          # resource group
ENV=fdpo-test-cae                        # Container Apps environment
APP=test-perp-meta-ingestor              # the new app
TAG=$(git rev-parse --short HEAD)
IMAGE=$ACR.azurecr.io/perp-meta-ingestor:$TAG

# 1) build & push
az acr build -r $ACR \
  -f infra/Dockerfile.perp_meta_ingestor \
  -t perp-meta-ingestor:$TAG .

# 2) create (first time) — uses managed identity for Redis AAD auth
#    if your Redis is configured with Entra ID. Pick ONE of the auth combos:
az containerapp create \
  --resource-group $RG \
  --name $APP \
  --environment $ENV \
  --image $IMAGE \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.25 --memory 0.5Gi \
  --system-assigned \
  --env-vars \
      REDIS_HOST=$REDIS_HOST \
      REDIS_USERNAME=$REDIS_USERNAME \
      MFP_SYMBOLS=BTCUSDT \
      MFP_POLL_SECONDS=300 \
      MFP_FUNDING_POLL_SECONDS=1800 \
      MFP_TRIM_HOURS=30

# 3) update on subsequent commits
az containerapp update --resource-group $RG --name $APP --image $IMAGE
```

If using access-key auth instead, drop `--system-assigned` and pass
`REDIS_HOST` + a `redis-password` secret resolved as `REDIS_PASSWORD`.

## Verification

```bash
# Use redis-cli with AAD or password as configured for your Redis.
redis-cli -u "$REDIS_URL" ZCARD funding:BTCUSDT:hist   # expect ~12 entries (4 days * 3/day)
redis-cli -u "$REDIS_URL" ZCARD taker:BTCUSDT:hist     # expect ~360 entries within an hour
redis-cli -u "$REDIS_URL" ZCARD lsr:BTCUSDT:hist       # expect ~360 entries within an hour

# inspect newest entries
redis-cli -u "$REDIS_URL" ZREVRANGE taker:BTCUSDT:hist 0 4 WITHSCORES
```

Members look like:

```
funding:BTCUSDT:hist  "1772064000000:0.00010000"
taker:BTCUSDT:hist    "1777420800000:0.894999"
lsr:BTCUSDT:hist      "1777420800000:0.94571604"
```

## Local dev

```powershell
$env:REDIS_URL = "redis://localhost:6379/0"
$env:PYTHONPATH = "$PWD/src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -u scripts\perp_meta_ingestor.py
```

Logs every 5 min (and once at startup after backfill); look for
`backfill: <indicator>/<symbol> rows=<n>` then `poll: ... upserted=<n>`.

## Strategy hand-off

`MultiFactorPortfolioStrategy` reads from these ZSETs in **live mode** via
`src/indicators/perp_meta_provider.py`:

```python
from indicators.perp_meta_provider import (
    get_funding_provider, get_taker_provider, get_lsr_provider,
)
funding = get_funding_provider("BTCUSDT")           # Redis-backed in live, parquet-backed in backtest
funding.value_at(ts_ms)                              # latest <= ts_ms
funding.range(start_ms, end_ms)                      # bulk gap-fill
```

Mode auto-detects from env (`MFP_PROVIDER_MODE=backtest|live`, or
`REDIS_URL`/`REDIS_HOST` set => live). The runner exports these on startup.

### Required runner env vars (in addition to Redis)

For live MFP backtest *seed* data the runner reads the existing parquets via
the same blob fallback used in backtest:

```
MFP_PARQUET_BLOB_CONTAINER=market-data
MFP_PARQUET_BLOB_PREFIX=perp_meta
AZURE_BLOB_ACCOUNT_URL=https://teststrategies.blob.core.windows.net/
```

The strategy seeds its in-memory unified dataset from these blobs at
`initialize()` and then keeps it fresh by appending live values from the
providers as new 15m bars close.
