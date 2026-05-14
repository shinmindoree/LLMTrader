"""OI Ingestor — long-running poller that publishes BTCUSDT 5m Open Interest
history to Redis as a sorted set.

Lifecycle:
  - On startup: deep backfill of OI_BACKFILL_HOURS (default = OI_TRIM_HOURS)
    via paginated `/futures/data/openInterestHist?period=5m` requests.
  - Loop: every OI_POLL_SECONDS, fetch the most recent points and ZADD them.
  - Every OI_GAPFILL_INTERVAL_SECONDS, scan the ZSET for holes inside the
    retention window and re-fetch the missing slots from Binance.
  - Trim the sorted set to the most recent OI_TRIM_HOURS to bound memory.

Why gap-fill + deep backfill:
  ``MultiFactorPortfolioStrategy`` reads up to ~60 days of OI when it warms
  up its 30m/60m/240m resampled views in live mode. Earlier defaults capped
  the ZSET at 30h, leaving ~70% of live-mode lookups returning NaN and
  causing the strategy to silently skip signal evaluations on bars where
  the corresponding backtest produced trades. See
  ``scripts/verify_perp_meta_drift.py`` for the diagnostic.

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
  OI_SYMBOLS                       comma-separated, default BTCUSDT
  OI_POLL_SECONDS                  default 300 (5 minutes)
  OI_TRIM_HOURS                    default 720 (30 days; retention window)
  OI_BACKFILL_HOURS                default = OI_TRIM_HOURS
  OI_GAPFILL_INTERVAL_SECONDS      default 1800 (30 min)
  OI_GAPFILL_WINDOW_HOURS          default 168 (1 week; how far back to scan)
  OI_COVERAGE_LOG_HOURS            default 24 (log coverage_pct over this window)
  BINANCE_FAPI                     default https://fapi.binance.com

Optional backtest-parquet refresh (off when blob env not set):
  OI_PARQUET_REFRESH_HOURS         default 6 (0 disables)
  OI_PARQUET_BLOB_CONTAINER        e.g. market-data
  OI_PARQUET_BLOB_NAME_{SYMBOL}    e.g. perp_meta/BTCUSDT_oi_5m.parquet
  AZURE_BLOB_ACCOUNT_URL or AZURE_BLOB_CONNECTION_STRING
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

# refresh_oi_parquet sits next to this script in scripts/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
try:
    from refresh_oi_parquet import refresh_oi_parquet  # type: ignore[import-not-found]
except Exception as _exc:  # noqa: BLE001
    refresh_oi_parquet = None  # type: ignore[assignment]
    _REFRESH_IMPORT_ERR = _exc
else:
    _REFRESH_IMPORT_ERR = None

logger = logging.getLogger("oi_ingestor")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("azure").setLevel(logging.WARNING)

BINANCE_FAPI = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")
REDIS_KEY_FMT = "oi:{symbol}:hist"
PERIOD_5M_MS = 5 * 60 * 1000
BINANCE_OI_LIMIT = 500  # /futures/data/openInterestHist hard cap
# Binance /futures/data/* endpoints only serve the most recent 30 days.
BINANCE_OI_LOOKBACK_MS = 30 * 24 * 3600 * 1000

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
    if trim_after_ms > 0:
        pipe.zremrangebyscore(key, "-inf", trim_after_ms)
    pipe.execute()
    return n


def _fetch_paginated(http: httpx.Client, symbol: str,
                    start_ms: int, end_ms: int) -> list[dict]:
    """Page through ``/futures/data/openInterestHist`` 500 rows at a time
    until the requested window is covered.

    Binance returns rows oldest-first when ``startTime`` is set. We advance
    ``start_ms`` past the last returned timestamp and keep pulling until
    either an empty page or the end of the window is reached.
    """
    out: list[dict] = []
    cursor = max(start_ms, end_ms - BINANCE_OI_LOOKBACK_MS)
    page = 0
    while cursor < end_ms and page < 200:  # 200 pages * 500 rows = 100k cap
        if _shutdown:
            break
        page += 1
        rows = fetch_oi_5m(http, symbol, cursor, end_ms)
        if not rows:
            break
        out.extend(rows)
        last_ts = int(rows[-1]["timestamp"])
        # Advance one period past last returned ts to avoid duplicate pages.
        next_cursor = last_ts + PERIOD_5M_MS
        if next_cursor <= cursor:
            # No forward progress; bail to avoid infinite loop.
            break
        cursor = next_cursor
        # Stop early if Binance returned fewer than the page cap.
        if len(rows) < BINANCE_OI_LIMIT:
            break
    return out


def _existing_ts_set(rd, symbol: str, start_ms: int, end_ms: int) -> set[int]:
    """Return the set of 5m-bucket timestamps already stored in Redis for
    ``[start_ms, end_ms]``. Reads scores only — cheap even at 30d depth.
    """
    key = REDIS_KEY_FMT.format(symbol=symbol)
    try:
        # Each member is "{ts}:{val}"; score is ts. Reading scores is enough.
        pairs = rd.zrangebyscore(key, start_ms, end_ms, withscores=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("zrangebyscore failed for %s: %s", key, exc)
        return set()
    return {int(score) for _, score in pairs}


def _expected_ts(start_ms: int, end_ms: int) -> list[int]:
    """Aligned 5m buckets in ``[start_ms, end_ms)`` inclusive of start."""
    first = (start_ms // PERIOD_5M_MS) * PERIOD_5M_MS
    if first < start_ms:
        first += PERIOD_5M_MS
    last = (end_ms // PERIOD_5M_MS) * PERIOD_5M_MS
    if first > last:
        return []
    return list(range(first, last + 1, PERIOD_5M_MS))


def coverage_pct(rd, symbol: str, hours: int) -> tuple[float, int, int]:
    end = int(time.time() * 1000)
    start = end - hours * 3600 * 1000
    expected = _expected_ts(start, end)
    if not expected:
        return 1.0, 0, 0
    have = _existing_ts_set(rd, symbol, start, end)
    have_count = sum(1 for t in expected if t in have)
    return (have_count / len(expected)), have_count, len(expected)


def gap_fill(rd, http: httpx.Client, symbol: str, *,
             window_hours: int, trim_hours: int) -> int:
    """Scan the ZSET for missing 5m buckets inside the last ``window_hours``
    and re-fetch them from Binance.

    The most recent ~10 minutes are skipped because Binance publishes 5m
    bars with a small delay and we don't want to count them as "missing".
    """
    now = int(time.time() * 1000)
    end = now - 10 * 60 * 1000  # leave the freshest bars to the regular poll
    start = now - window_hours * 3600 * 1000
    if start >= end:
        return 0
    expected = _expected_ts(start, end)
    if not expected:
        return 0
    have = _existing_ts_set(rd, symbol, start, end)
    missing = [t for t in expected if t not in have]
    if not missing:
        return 0
    # Compact the missing list into contiguous ranges so each Binance call
    # covers as much as possible.
    ranges: list[tuple[int, int]] = []
    run_start = missing[0]
    run_end = run_start
    for t in missing[1:]:
        if t == run_end + PERIOD_5M_MS:
            run_end = t
        else:
            ranges.append((run_start, run_end))
            run_start = run_end = t
    ranges.append((run_start, run_end))
    logger.info(
        "gap-fill %s: missing=%d in last %dh → %d ranges (first=%s..%s)",
        symbol, len(missing), window_hours, len(ranges),
        run_start_first := ranges[0][0], ranges[0][1],
    )
    fetched = 0
    trim_after = now - trim_hours * 3600 * 1000
    for rs, re_ in ranges:
        if _shutdown:
            break
        # Pad the range edges to ensure the bucketing matches Binance's.
        rows = _fetch_paginated(http, symbol, rs - PERIOD_5M_MS, re_ + PERIOD_5M_MS)
        if rows:
            fetched += publish(rd, symbol, rows, trim_after_ms=trim_after)
    return fetched


def backfill_initial(rd, http: httpx.Client, symbol: str,
                     hours: int) -> int:
    end = int(time.time() * 1000)
    start = end - hours * 3600 * 1000
    logger.info(
        "backfill: %s hours=%d (paginated) start=%d end=%d",
        symbol, hours, start, end,
    )
    rows = _fetch_paginated(http, symbol, start, end)
    if not rows:
        logger.warning("backfill: no rows received for %s", symbol)
        return 0
    n = publish(rd, symbol, rows, trim_after_ms=start)
    logger.info(
        "backfill: %s rows=%d range=%d..%d (pages~%d)",
        symbol, n, int(rows[0]["timestamp"]), int(rows[-1]["timestamp"]),
        (len(rows) + BINANCE_OI_LIMIT - 1) // BINANCE_OI_LIMIT,
    )
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
    trim_hours = int(os.environ.get("OI_TRIM_HOURS", "720"))  # 30 days default
    backfill_hours = int(os.environ.get("OI_BACKFILL_HOURS", str(trim_hours)))
    gapfill_interval = int(os.environ.get("OI_GAPFILL_INTERVAL_SECONDS", "1800"))
    gapfill_window_hours = int(os.environ.get("OI_GAPFILL_WINDOW_HOURS", "168"))
    coverage_log_hours = int(os.environ.get("OI_COVERAGE_LOG_HOURS", "24"))
    refresh_hours = float(os.environ.get("OI_PARQUET_REFRESH_HOURS", "6"))
    blob_container = os.environ.get("OI_PARQUET_BLOB_CONTAINER", "").strip()
    parquet_refresh_enabled = (
        refresh_hours > 0 and bool(blob_container) and refresh_oi_parquet is not None
    )
    if refresh_hours > 0 and refresh_oi_parquet is None:
        logger.warning(
            "OI_PARQUET_REFRESH_HOURS set but refresh_oi_parquet import failed: %s",
            _REFRESH_IMPORT_ERR,
        )
    if parquet_refresh_enabled:
        logger.info(
            "parquet refresh enabled: every %.1fh -> blob container=%s",
            refresh_hours, blob_container,
        )
    else:
        logger.info("parquet refresh disabled")

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
    logger.info(
        "connected to redis; symbols=%s poll=%ds trim=%dh backfill=%dh "
        "gapfill_every=%ds gapfill_window=%dh",
        symbols, poll_seconds, trim_hours, backfill_hours,
        gapfill_interval, gapfill_window_hours,
    )

    with httpx.Client() as http:
        # 1) Initial backfill (paginated to cover the full retention window).
        for sym in symbols:
            try:
                backfill_initial(rd, http, sym, hours=backfill_hours)
            except Exception as exc:  # noqa: BLE001
                logger.error("backfill failed for %s: %s", sym, exc)
            try:
                pct, have, total = coverage_pct(rd, sym, hours=coverage_log_hours)
                logger.info(
                    "coverage %s last_%dh: %.1f%% (%d/%d)",
                    sym, coverage_log_hours, pct * 100.0, have, total,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("coverage check failed for %s: %s", sym, exc)

        # 2) Poll loop
        next_run = time.time()
        # Schedule first parquet refresh ~5 min after startup so we don't
        # block the initial Redis backfill, then every refresh_hours.
        next_parquet_refresh = (
            time.time() + 300.0 if parquet_refresh_enabled else float("inf")
        )
        # Gap-fill runs shortly after the first poll to give the deep
        # backfill above a head-start.
        next_gapfill = time.time() + gapfill_interval
        while not _shutdown:
            for sym in symbols:
                try:
                    loop_once(rd, http, sym, trim_hours=trim_hours)
                except Exception as exc:  # noqa: BLE001
                    logger.error("poll failed for %s: %s", sym, exc)

            # Periodic gap-fill: cheap when there are no holes (ZRANGE only).
            if time.time() >= next_gapfill:
                for sym in symbols:
                    try:
                        n_gap = gap_fill(
                            rd, http, sym,
                            window_hours=gapfill_window_hours,
                            trim_hours=trim_hours,
                        )
                        if n_gap:
                            logger.info(
                                "gap-fill %s: re-fetched=%d rows", sym, n_gap,
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("gap-fill failed for %s: %s", sym, exc)
                    try:
                        pct, have, total = coverage_pct(
                            rd, sym, hours=coverage_log_hours,
                        )
                        logger.info(
                            "coverage %s last_%dh: %.1f%% (%d/%d)",
                            sym, coverage_log_hours, pct * 100.0, have, total,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("coverage check failed for %s: %s", sym, exc)
                next_gapfill = time.time() + gapfill_interval

            # Backtest parquet refresh (best-effort; failures never abort poll).
            if parquet_refresh_enabled and time.time() >= next_parquet_refresh:
                for sym in symbols:
                    blob_name = os.environ.get(f"OI_PARQUET_BLOB_NAME_{sym}", "").strip()
                    if not blob_name:
                        logger.warning(
                            "parquet refresh skipped for %s: OI_PARQUET_BLOB_NAME_%s not set",
                            sym, sym,
                        )
                        continue
                    try:
                        result = refresh_oi_parquet(  # type: ignore[misc]
                            symbol=sym,
                            blob_container_name=blob_container,
                            blob_name=blob_name,
                        )
                        logger.info("parquet refresh ok %s: %s", sym, result)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("parquet refresh failed for %s: %s", sym, exc)
                next_parquet_refresh = time.time() + refresh_hours * 3600.0

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
