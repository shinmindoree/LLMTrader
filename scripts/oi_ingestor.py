"""OI Ingestor — long-running poller that publishes BTCUSDT 5m Open Interest
history to Redis as a sorted set.

Lifecycle:
  - On startup: backfill last 25h via `/futures/data/openInterestHist?period=5m`
  - Loop: every 5 minutes, fetch the most recent points and ZADD them
  - Trim the sorted set to the most recent ~30h (360 points) to bound memory

Redis schema:
  Key:    oi:{SYMBOL}:hist        (sorted set)
  Member: "{ts_ms}:{sum_oi}"      (string)
  Score:  ts_ms                   (int64 millis)

Run as a separate Container Apps Job/long-running container (see infra/Dockerfile.oi_ingestor).

Env:
    REDIS_URL       Redis URL for key-based auth
    REDIS_HOST      alternative to REDIS_URL, used with REDIS_PASSWORD or REDIS_USERNAME
    REDIS_PASSWORD  key-based password for REDIS_HOST
    REDIS_USERNAME  Entra ID Redis username/object id for REDIS_HOST
  OI_SYMBOLS      comma-separated, default BTCUSDT
  OI_POLL_SECONDS default 300 (5 minutes)
  OI_TRIM_HOURS   default 30  (sorted set retention)
  BINANCE_FAPI    default https://fapi.binance.com
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

logger = logging.getLogger("oi_ingestor")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("azure").setLevel(logging.WARNING)

BINANCE_FAPI = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")
REDIS_KEY_FMT = "oi:{symbol}:hist"

_shutdown = False


def _on_signal(signum, frame):  # noqa: ANN001
    global _shutdown
    logger.info("received signal=%s, draining...", signum)
    _shutdown = True


def fetch_oi_5m(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch openInterestHist period=5m within [start_ms, end_ms]. Single request,
    paginated by caller if needed. Binance hard-caps limit at 500.
    """
    params = {
        "symbol": symbol,
        "period": "5m",
        "limit": 500,
        "startTime": int(start_ms),
        "endTime": int(end_ms),
    }
    for attempt in range(5):
        try:
            resp = client.get(f"{BINANCE_FAPI}/futures/data/openInterestHist",
                              params=params, timeout=15.0)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt
                logger.warning("oi fetch http=%s, sleep %ds", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() or []
        except httpx.HTTPError as exc:
            logger.warning("oi fetch error attempt=%d: %s", attempt + 1, exc)
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    return []


def publish(rd, symbol: str, rows: Iterable[dict],
            trim_after_ms: int) -> int:
    key = REDIS_KEY_FMT.format(symbol=symbol)
    pipe = rd.pipeline()
    n = 0
    for r in rows:
        try:
            ts = int(r["timestamp"])
            oi = float(r["sumOpenInterest"])
        except Exception:  # noqa: BLE001
            continue
        member = f"{ts}:{oi:.6f}"
        pipe.zadd(key, {member: ts})
        n += 1
    pipe.zremrangebyscore(key, "-inf", trim_after_ms)
    pipe.execute()
    return n


def backfill_initial(rd, http: httpx.Client, symbol: str,
                     hours: int = 25) -> int:
    end = int(time.time() * 1000)
    start = end - hours * 3600 * 1000
    rows = fetch_oi_5m(http, symbol, start, end)
    if not rows:
        logger.warning("backfill: no rows received for %s", symbol)
        return 0
    n = publish(rd, symbol, rows, trim_after_ms=start)
    logger.info("backfill: %s rows=%d range=%d..%d", symbol, n,
                int(rows[0]["timestamp"]), int(rows[-1]["timestamp"]))
    return n


def loop_once(rd, http: httpx.Client, symbol: str, trim_hours: int) -> int:
    end = int(time.time() * 1000)
    # over-fetch the last 30 minutes so we don't miss late-arriving 5m closes
    start = end - 30 * 60 * 1000
    rows = fetch_oi_5m(http, symbol, start, end)
    n = publish(rd, symbol, rows, trim_after_ms=end - trim_hours * 3600 * 1000)
    logger.info("poll: %s upserted=%d (key=%s)", symbol, n,
                REDIS_KEY_FMT.format(symbol=symbol))
    return n


def main() -> int:
    redis_url = os.environ.get("REDIS_URL", "").strip()
    redis_host = os.environ.get("REDIS_HOST", "").strip()
    redis_password = os.environ.get("REDIS_PASSWORD", "")
    redis_username = os.environ.get("REDIS_USERNAME", "").strip()
    if not redis_url and not (redis_host and (redis_password or redis_username)):
        logger.error("REDIS_URL, REDIS_HOST+REDIS_PASSWORD, or REDIS_HOST+REDIS_USERNAME is required")
        return 2

    symbols = [s.strip().upper() for s in os.environ.get("OI_SYMBOLS", "BTCUSDT").split(",") if s.strip()]
    poll_seconds = int(os.environ.get("OI_POLL_SECONDS", "300"))
    trim_hours = int(os.environ.get("OI_TRIM_HOURS", "30"))

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    if redis_host and redis_username:
        rd = create_redis_client_with_aad(
            host=redis_host,
            username=redis_username,
            port=int(os.environ.get("REDIS_PORT", "6380")),
            ssl=os.environ.get("REDIS_SSL", "true").strip().lower() != "false",
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
    elif redis_host and redis_password:
        rd = create_redis_client_from_parts(
            host=redis_host,
            port=int(os.environ.get("REDIS_PORT", "6380")),
            password=redis_password,
            ssl=os.environ.get("REDIS_SSL", "true").strip().lower() != "false",
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
    else:
        rd = create_redis_client(
            redis_url,
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
    rd.ping()
    logger.info("connected to redis; symbols=%s poll=%ds trim=%dh",
                symbols, poll_seconds, trim_hours)

    with httpx.Client() as http:
        # 1) Initial backfill
        for sym in symbols:
            try:
                backfill_initial(rd, http, sym, hours=trim_hours)
            except Exception as exc:  # noqa: BLE001
                logger.error("backfill failed for %s: %s", sym, exc)

        # 2) Poll loop
        next_run = time.time()
        while not _shutdown:
            for sym in symbols:
                try:
                    loop_once(rd, http, sym, trim_hours=trim_hours)
                except Exception as exc:  # noqa: BLE001
                    logger.error("poll failed for %s: %s", sym, exc)
            next_run += poll_seconds
            sleep_for = max(1.0, next_run - time.time())
            for _ in range(int(sleep_for)):
                if _shutdown:
                    break
                time.sleep(1)

    logger.info("oi_ingestor stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
