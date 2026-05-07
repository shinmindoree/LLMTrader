"""Perp-meta ingestor — long-running poller that publishes funding-rate /
taker buy-sell ratio / global long-short account ratio history to Redis as
sorted sets.

This is the live-data feed for ``MultiFactorPortfolioStrategy``'s
funding/taker/LSR signals. It mirrors ``oi_ingestor.py`` but covers three
indicators in a single process so we only deploy one Container App.

Lifecycle (per indicator, per symbol):
  - On startup: backfill last ~30h via the relevant Binance endpoint.
  - Loop: poll every ``MFP_POLL_SECONDS`` (default 300) and ZADD new rows.
  - Trim each ZSET to the most recent ``MFP_TRIM_HOURS`` (default 30h).

Redis schema (matches src/indicators/perp_meta_provider.py):
  funding:{SYMBOL}:hist  -> "{ts_ms}:{rate}"   from /fapi/v1/fundingRate
  taker:{SYMBOL}:hist    -> "{ts_ms}:{ratio}"  from /futures/data/takerlongshortRatio
  lsr:{SYMBOL}:hist      -> "{ts_ms}:{ratio}"  from /futures/data/globalLongShortAccountRatio

Env (Redis -- choose one auth path):
  REDIS_URL                  full URL with key auth
  REDIS_HOST + REDIS_USERNAME use Entra ID (managed identity) AAD
  REDIS_HOST + REDIS_PASSWORD legacy access key auth

Env (config):
  MFP_SYMBOLS         comma-separated, default BTCUSDT
  MFP_POLL_SECONDS    default 300 (5 min)
  MFP_TRIM_HOURS      default 30
  MFP_FUNDING_POLL_SECONDS  override funding-only cadence (default 1800s = 30 min)
  BINANCE_FAPI        default https://fapi.binance.com
  MFP_INDICATORS      comma-separated subset of {funding,taker,lsr}; default = all three
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Iterable

import httpx

from common.redis_client import (
    create_redis_client,
    create_redis_client_from_parts,
    create_redis_client_with_aad,
)

logger = logging.getLogger("perp_meta_ingestor")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("azure").setLevel(logging.WARNING)

BINANCE_FAPI = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")

# ---------------------------------------------------------------------------
# Indicator definitions
# ---------------------------------------------------------------------------
# Each indicator is a tuple of: (kind, redis_key_fmt, endpoint, ts_field, value_field, default_period)
INDICATORS: dict[str, dict] = {
    "funding": {
        "key_fmt": "funding:{symbol}:hist",
        "endpoint": "/fapi/v1/fundingRate",
        "ts_field": "fundingTime",
        "value_field": "fundingRate",
        "params": lambda symbol, start, end: {
            "symbol": symbol,
            "limit": 1000,
            "startTime": int(start),
            "endTime": int(end),
        },
        # Funding cadence is 8h. Backfill 4 days (12 events) is more than enough.
        "backfill_hours_default": 96,
    },
    "taker": {
        "key_fmt": "taker:{symbol}:hist",
        "endpoint": "/futures/data/takerlongshortRatio",
        "ts_field": "timestamp",
        "value_field": "buySellRatio",
        "params": lambda symbol, start, end: {
            "symbol": symbol,
            "period": "5m",
            "limit": 500,
            "startTime": int(start),
            "endTime": int(end),
        },
        "backfill_hours_default": 30,
    },
    "lsr": {
        "key_fmt": "lsr:{symbol}:hist",
        # globalLongShortAccountRatio gives `count_long_short_ratio` (across all accounts);
        # this is what the MFP strategy reads from the LSR parquet column.
        "endpoint": "/futures/data/globalLongShortAccountRatio",
        "ts_field": "timestamp",
        "value_field": "longShortRatio",
        "params": lambda symbol, start, end: {
            "symbol": symbol,
            "period": "5m",
            "limit": 500,
            "startTime": int(start),
            "endTime": int(end),
        },
        "backfill_hours_default": 30,
    },
}

_shutdown = False


def _on_signal(signum, frame):  # noqa: ANN001
    global _shutdown
    logger.info("received signal=%s, draining...", signum)
    _shutdown = True


# ---------------------------------------------------------------------------
# Binance fetch with retry/backoff
# ---------------------------------------------------------------------------
def _fetch_with_retry(client: httpx.Client, url: str, params: dict) -> list[dict]:
    for attempt in range(5):
        try:
            resp = client.get(url, params=params, timeout=20.0)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt
                logger.warning("fetch http=%s url=%s, sleep %ds",
                               resp.status_code, url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.HTTPError as exc:
            logger.warning("fetch error url=%s attempt=%d: %s", url, attempt + 1, exc)
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    return []


def fetch_indicator(client: httpx.Client, indicator: str, symbol: str,
                    start_ms: int, end_ms: int) -> list[dict]:
    cfg = INDICATORS[indicator]
    url = f"{BINANCE_FAPI}{cfg['endpoint']}"
    params = cfg["params"](symbol, start_ms, end_ms)
    return _fetch_with_retry(client, url, params)


# ---------------------------------------------------------------------------
# Publish to Redis
# ---------------------------------------------------------------------------
def publish(rd, indicator: str, symbol: str, rows: Iterable[dict],
            trim_after_ms: int) -> int:
    cfg = INDICATORS[indicator]
    key = cfg["key_fmt"].format(symbol=symbol)
    ts_field = cfg["ts_field"]
    val_field = cfg["value_field"]
    pipe = rd.pipeline()
    n = 0
    for r in rows:
        try:
            ts = int(r[ts_field])
            val = float(r[val_field])
        except Exception:  # noqa: BLE001
            continue
        # Stable string format: "{ts}:{val}". Redis ZSET dedupes by member,
        # so writing the same (ts, val) twice is a no-op.
        member = f"{ts}:{val:.10g}"
        pipe.zadd(key, {member: ts})
        n += 1
    if trim_after_ms > 0:
        pipe.zremrangebyscore(key, "-inf", trim_after_ms)
    pipe.execute()
    return n


def backfill_initial(rd, http: httpx.Client, indicator: str, symbol: str,
                     hours: int) -> int:
    end = int(time.time() * 1000)
    start = end - hours * 3600 * 1000
    rows = fetch_indicator(http, indicator, symbol, start, end)
    if not rows:
        logger.warning("backfill: no rows for %s/%s", indicator, symbol)
        return 0
    n = publish(rd, indicator, symbol, rows, trim_after_ms=start)
    logger.info("backfill: %s/%s rows=%d", indicator, symbol, n)
    return n


def loop_once(rd, http: httpx.Client, indicator: str, symbol: str,
              trim_hours: int) -> int:
    end = int(time.time() * 1000)
    cfg = INDICATORS[indicator]
    # Funding cadence is 8h; over-fetch the last 12h to catch any late tails.
    if indicator == "funding":
        start = end - 12 * 3600 * 1000
    else:
        start = end - 30 * 60 * 1000
    rows = fetch_indicator(http, indicator, symbol, start, end)
    n = publish(rd, indicator, symbol, rows,
                trim_after_ms=end - trim_hours * 3600 * 1000)
    key = cfg["key_fmt"].format(symbol=symbol)
    logger.info("poll: %s/%s upserted=%d (key=%s)", indicator, symbol, n, key)
    return n


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _build_redis_client():
    redis_url = os.environ.get("REDIS_URL", "").strip()
    redis_host = os.environ.get("REDIS_HOST", "").strip()
    redis_password = os.environ.get("REDIS_PASSWORD", "")
    redis_username = os.environ.get("REDIS_USERNAME", "").strip()
    if not redis_url and not (redis_host and (redis_password or redis_username)):
        raise RuntimeError(
            "REDIS_URL, REDIS_HOST+REDIS_PASSWORD, or REDIS_HOST+REDIS_USERNAME is required"
        )
    if redis_host and redis_username:
        return create_redis_client_with_aad(
            host=redis_host,
            username=redis_username,
            port=int(os.environ.get("REDIS_PORT", "6380")),
            ssl=os.environ.get("REDIS_SSL", "true").strip().lower() != "false",
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
    if redis_host and redis_password:
        return create_redis_client_from_parts(
            host=redis_host,
            port=int(os.environ.get("REDIS_PORT", "6380")),
            password=redis_password,
            ssl=os.environ.get("REDIS_SSL", "true").strip().lower() != "false",
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
    return create_redis_client(
        redis_url,
        socket_connect_timeout=5,
        socket_timeout=5,
        decode_responses=True,
    )


def main() -> int:
    try:
        rd = _build_redis_client()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 2

    enabled = [s.strip().lower() for s in os.environ.get(
        "MFP_INDICATORS", "funding,taker,lsr"
    ).split(",") if s.strip()]
    enabled = [k for k in enabled if k in INDICATORS]
    if not enabled:
        logger.error("no valid MFP_INDICATORS provided")
        return 2

    symbols = [s.strip().upper() for s in os.environ.get(
        "MFP_SYMBOLS", "BTCUSDT"
    ).split(",") if s.strip()]
    poll_seconds = int(os.environ.get("MFP_POLL_SECONDS", "300"))
    funding_poll_seconds = int(os.environ.get("MFP_FUNDING_POLL_SECONDS", "1800"))
    trim_hours = int(os.environ.get("MFP_TRIM_HOURS", "30"))

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        rd.ping()
    except Exception as exc:  # noqa: BLE001
        logger.error("redis ping failed: %s", exc)
        return 3
    logger.info(
        "connected to redis; symbols=%s indicators=%s poll=%ds funding_poll=%ds trim=%dh",
        symbols, enabled, poll_seconds, funding_poll_seconds, trim_hours,
    )

    with httpx.Client() as http:
        # 1) Initial backfill.
        for ind in enabled:
            hours = INDICATORS[ind].get("backfill_hours_default", trim_hours)
            for sym in symbols:
                try:
                    backfill_initial(rd, http, ind, sym, hours=hours)
                except Exception as exc:  # noqa: BLE001
                    logger.error("backfill failed %s/%s: %s", ind, sym, exc)

        # 2) Per-indicator next-run schedule. Funding is on its own slower cadence.
        now = time.time()
        next_run: dict[str, float] = {}
        for ind in enabled:
            next_run[ind] = now + (
                funding_poll_seconds if ind == "funding" else poll_seconds
            )

        # 3) Poll loop.
        while not _shutdown:
            now = time.time()
            ran_any = False
            for ind in enabled:
                if now >= next_run[ind]:
                    for sym in symbols:
                        try:
                            loop_once(rd, http, ind, sym, trim_hours=trim_hours)
                        except Exception as exc:  # noqa: BLE001
                            logger.error("poll failed %s/%s: %s", ind, sym, exc)
                    cadence = (
                        funding_poll_seconds if ind == "funding" else poll_seconds
                    )
                    next_run[ind] = now + cadence
                    ran_any = True
            # Sleep until the next earliest run, but at least 1s and at most 60s
            # so SIGTERM can interrupt promptly.
            if _shutdown:
                break
            sleep_for = max(1.0, min(60.0, min(next_run.values()) - time.time()))
            if not ran_any:
                # Smaller chunked sleep so SIGTERM is responsive.
                slept = 0.0
                while slept < sleep_for and not _shutdown:
                    time.sleep(min(1.0, sleep_for - slept))
                    slept += 1.0

    logger.info("perp_meta_ingestor stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
