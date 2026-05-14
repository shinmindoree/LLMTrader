"""Perp-meta ingestor — long-running poller that publishes funding-rate /
taker buy-sell ratio / global long-short account ratio history to Redis as
sorted sets.

This is the live-data feed for ``MultiFactorPortfolioStrategy``'s
funding/taker/LSR signals. It mirrors ``oi_ingestor.py`` but covers three
indicators in a single process so we only deploy one Container App.

Lifecycle (per indicator, per symbol):
  - On startup: deep backfill of ``MFP_BACKFILL_HOURS`` (default =
    ``MFP_TRIM_HOURS``) via paginated Binance requests.
  - Loop: poll every ``MFP_POLL_SECONDS`` (default 300) and ZADD new rows.
  - Every ``MFP_GAPFILL_INTERVAL_SECONDS`` seconds, scan each ZSET for
    missing buckets inside ``MFP_GAPFILL_WINDOW_HOURS`` and re-fetch them.
  - Trim each ZSET to the most recent ``MFP_TRIM_HOURS``.

Why gap-fill + deep backfill:
  ``MultiFactorPortfolioStrategy`` reads up to ~60 days of funding/taker/LSR
  when it warms up its 30m/60m/240m resampled views in live mode. Older
  defaults capped the ZSETs at 30h, leaving ~70% of live lookups returning
  NaN and causing the strategy to silently drop signals that the backtest
  (which reads the full-history parquet) was producing.

Redis schema (matches src/indicators/perp_meta_provider.py):
  funding:{SYMBOL}:hist  -> "{ts_ms}:{rate}"   from /fapi/v1/fundingRate
  taker:{SYMBOL}:hist    -> "{ts_ms}:{ratio}"  from /futures/data/takerlongshortRatio
  lsr:{SYMBOL}:hist      -> "{ts_ms}:{ratio}"  from /futures/data/globalLongShortAccountRatio

Env (Redis -- choose one auth path):
  REDIS_URL                  full URL with key auth
  REDIS_HOST + REDIS_USERNAME use Entra ID (managed identity) AAD
  REDIS_HOST + REDIS_PASSWORD legacy access key auth

Env (config):
  MFP_SYMBOLS                      comma-separated, default BTCUSDT
  MFP_POLL_SECONDS                 default 300 (5 min)
  MFP_TRIM_HOURS                   default 720 (30 days; retention window)
  MFP_BACKFILL_HOURS               default = MFP_TRIM_HOURS
  MFP_GAPFILL_INTERVAL_SECONDS     default 1800 (30 min)
  MFP_GAPFILL_WINDOW_HOURS         default 168 (1 week)
  MFP_COVERAGE_LOG_HOURS           default 24
  MFP_FUNDING_POLL_SECONDS         override funding-only cadence (default 1800s)
  BINANCE_FAPI                     default https://fapi.binance.com
  MFP_INDICATORS                   comma-separated subset of {funding,taker,lsr}

Optional backtest-parquet refresh (off when blob env not set):
  MFP_PARQUET_REFRESH_HOURS        default 6 (0 disables)
  MFP_PARQUET_KINDS                comma-separated subset of {funding,taker,lsr,klines}
                                   default = funding,taker,lsr,klines
  MFP_PARQUET_BLOB_CONTAINER       e.g. market-data
  MFP_PARQUET_BLOB_PREFIX          e.g. perp_meta (joined with filename)
  MFP_PARQUET_BLOB_NAME_<KIND>_<SYMBOL>  per-kind override
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

# refresh_perp_meta_parquet sits next to this script in scripts/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
try:
    from refresh_perp_meta_parquet import (  # type: ignore[import-not-found]
        refresh_perp_meta_parquet,
    )
except Exception as _exc:  # noqa: BLE001
    refresh_perp_meta_parquet = None  # type: ignore[assignment]
    _PARQUET_IMPORT_ERR = _exc
else:
    _PARQUET_IMPORT_ERR = None

logger = logging.getLogger("perp_meta_ingestor")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("azure").setLevel(logging.WARNING)

BINANCE_FAPI = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")

# Binance ``/futures/data/*`` endpoints only serve the most recent 30 days.
# The server enforces the cutoff against its own clock, so a ``startTime``
# that is exactly ``now - 30d`` (per the client) lands a few hundred ms
# outside the window and yields ``parameter 'startTime' is invalid``
# (code -1130). Pull back 30 minutes for safe headroom against clock skew.
BINANCE_FUTURES_DATA_LOOKBACK_MS = 30 * 24 * 3600 * 1000 - 30 * 60 * 1000

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
        # Funding cadence is 8h.
        "period_ms": 8 * 3600 * 1000,
        # /fapi/v1/fundingRate caps at 1000 rows (= ~333 days @ 8h cadence),
        # so a single call is enough for our retention window. ``None``
        # disables strict pagination (we still loop until empty).
        "binance_limit": 1000,
        # /fapi/v1/* has no 30-day lookback cap (unlike /futures/data/*).
        "lookback_cap_ms": None,
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
        "period_ms": 5 * 60 * 1000,
        "binance_limit": 500,
        "lookback_cap_ms": BINANCE_FUTURES_DATA_LOOKBACK_MS,
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
        "period_ms": 5 * 60 * 1000,
        "binance_limit": 500,
        "lookback_cap_ms": BINANCE_FUTURES_DATA_LOOKBACK_MS,
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


def _fetch_indicator_paginated(client: httpx.Client, indicator: str, symbol: str,
                               start_ms: int, end_ms: int) -> list[dict]:
    """Page through ``fetch_indicator`` so deep windows can be backfilled
    despite the Binance per-request row cap.

    Binance returns oldest-first when ``startTime`` is provided. We advance
    the cursor past the last returned timestamp + one indicator period to
    avoid duplicate pages, and bail when an empty page or a short page is
    returned.
    """
    cfg = INDICATORS[indicator]
    period_ms = int(cfg["period_ms"])
    binance_limit = int(cfg["binance_limit"])
    lookback_cap_ms = cfg.get("lookback_cap_ms")
    cursor = start_ms
    if lookback_cap_ms is not None:
        cursor = max(cursor, end_ms - int(lookback_cap_ms))
    out: list[dict] = []
    ts_field = cfg["ts_field"]
    page = 0
    while cursor < end_ms and page < 200:
        if _shutdown:
            break
        page += 1
        rows = fetch_indicator(client, indicator, symbol, cursor, end_ms)
        if not rows:
            break
        out.extend(rows)
        try:
            last_ts = int(rows[-1][ts_field])
        except Exception:  # noqa: BLE001
            break
        next_cursor = last_ts + period_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(rows) < binance_limit:
            break
    return out


def _existing_ts_set(rd, indicator: str, symbol: str,
                     start_ms: int, end_ms: int) -> set[int]:
    key = INDICATORS[indicator]["key_fmt"].format(symbol=symbol)
    try:
        pairs = rd.zrangebyscore(key, start_ms, end_ms, withscores=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("zrangebyscore failed for %s: %s", key, exc)
        return set()
    return {int(score) for _, score in pairs}


def _expected_ts(indicator: str, start_ms: int, end_ms: int) -> list[int]:
    period_ms = int(INDICATORS[indicator]["period_ms"])
    if period_ms <= 0:
        return []
    first = (start_ms // period_ms) * period_ms
    if first < start_ms:
        first += period_ms
    last = (end_ms // period_ms) * period_ms
    if first > last:
        return []
    return list(range(first, last + 1, period_ms))


def coverage_pct(rd, indicator: str, symbol: str,
                 hours: int) -> tuple[float, int, int]:
    end = int(time.time() * 1000)
    start = end - hours * 3600 * 1000
    expected = _expected_ts(indicator, start, end)
    if not expected:
        return 1.0, 0, 0
    have = _existing_ts_set(rd, indicator, symbol, start, end)
    # For funding (8h cadence) ``expected`` already aligns to the cadence; we
    # report the fraction of expected timestamps that have a stored value.
    have_count = sum(1 for t in expected if t in have)
    return (have_count / len(expected)), have_count, len(expected)


def gap_fill(rd, http: httpx.Client, indicator: str, symbol: str, *,
             window_hours: int, trim_hours: int) -> int:
    """Scan the ZSET for missing buckets inside the last ``window_hours``
    and re-fetch them from Binance.

    For 5m indicators (taker/lsr) this is a direct grid check. For funding
    (8h cadence) Binance does not always emit *exactly* on the canonical
    grid (delays / first event timestamp differs by symbol). We treat any
    timestamp within ±10% of the period as "covered".
    """
    cfg = INDICATORS[indicator]
    period_ms = int(cfg["period_ms"])
    now = int(time.time() * 1000)
    # leave the freshest bars to the regular poll
    end = now - max(10 * 60 * 1000, period_ms)
    start = now - window_hours * 3600 * 1000
    lookback_cap_ms = cfg.get("lookback_cap_ms")
    if lookback_cap_ms is not None:
        start = max(start, now - int(lookback_cap_ms))
    if start >= end:
        return 0
    expected = _expected_ts(indicator, start, end)
    if not expected:
        return 0
    have_scores = _existing_ts_set(rd, indicator, symbol, start, end)
    if indicator == "funding":
        # Forgive small grid drift: a funding event within ±period/2 of an
        # expected ts counts as present.
        tolerance = period_ms // 2
        sorted_have = sorted(have_scores)
        missing: list[int] = []
        i = 0
        for t in expected:
            while i < len(sorted_have) and sorted_have[i] < t - tolerance:
                i += 1
            if i >= len(sorted_have) or sorted_have[i] > t + tolerance:
                missing.append(t)
    else:
        missing = [t for t in expected if t not in have_scores]
    if not missing:
        return 0
    # Coalesce missing timestamps into contiguous runs.
    ranges: list[tuple[int, int]] = []
    run_start = missing[0]
    run_end = run_start
    for t in missing[1:]:
        if t == run_end + period_ms:
            run_end = t
        else:
            ranges.append((run_start, run_end))
            run_start = run_end = t
    ranges.append((run_start, run_end))
    logger.info(
        "gap-fill %s/%s: missing=%d in last %dh → %d ranges (first=%s..%s)",
        indicator, symbol, len(missing), window_hours, len(ranges),
        ranges[0][0], ranges[0][1],
    )
    fetched = 0
    trim_after = now - trim_hours * 3600 * 1000
    for rs, re_ in ranges:
        if _shutdown:
            break
        rows = _fetch_indicator_paginated(
            http, indicator, symbol, rs - period_ms, re_ + period_ms,
        )
        if rows:
            fetched += publish(
                rd, indicator, symbol, rows, trim_after_ms=trim_after,
            )
    return fetched


def backfill_initial(rd, http: httpx.Client, indicator: str, symbol: str,
                     hours: int) -> int:
    end = int(time.time() * 1000)
    start = end - hours * 3600 * 1000
    logger.info(
        "backfill: %s/%s hours=%d (paginated) start=%d end=%d",
        indicator, symbol, hours, start, end,
    )
    rows = _fetch_indicator_paginated(http, indicator, symbol, start, end)
    if not rows:
        logger.warning("backfill: no rows for %s/%s", indicator, symbol)
        return 0
    n = publish(rd, indicator, symbol, rows, trim_after_ms=start)
    logger.info("backfill: %s/%s rows=%d (~%d pages)",
                indicator, symbol, n,
                (len(rows) + INDICATORS[indicator]["binance_limit"] - 1)
                // INDICATORS[indicator]["binance_limit"])
    return n


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
    trim_hours = int(os.environ.get("MFP_TRIM_HOURS", "720"))  # 30 days default
    backfill_hours = int(os.environ.get("MFP_BACKFILL_HOURS", str(trim_hours)))
    gapfill_interval = int(os.environ.get("MFP_GAPFILL_INTERVAL_SECONDS", "1800"))
    gapfill_window_hours = int(os.environ.get("MFP_GAPFILL_WINDOW_HOURS", "168"))
    coverage_log_hours = int(os.environ.get("MFP_COVERAGE_LOG_HOURS", "24"))

    # Optional parquet refresh config (writes back to blob storage so backtests
    # via the API/UI keep seeing fresh history; mirrors oi_ingestor pattern).
    parquet_refresh_hours = float(os.environ.get("MFP_PARQUET_REFRESH_HOURS", "6"))
    parquet_kinds = [
        k.strip().lower()
        for k in os.environ.get("MFP_PARQUET_KINDS", "funding,taker,lsr,klines").split(",")
        if k.strip()
    ]
    parquet_blob_container = os.environ.get("MFP_PARQUET_BLOB_CONTAINER", "").strip()
    parquet_blob_prefix = os.environ.get("MFP_PARQUET_BLOB_PREFIX", "").strip()
    parquet_refresh_enabled = (
        parquet_refresh_hours > 0
        and bool(parquet_blob_container)
        and refresh_perp_meta_parquet is not None
        and bool(parquet_kinds)
    )
    if parquet_refresh_hours > 0 and refresh_perp_meta_parquet is None:
        logger.warning(
            "MFP_PARQUET_REFRESH_HOURS set but refresh_perp_meta_parquet import failed: %s",
            _PARQUET_IMPORT_ERR,
        )
    if parquet_refresh_enabled:
        logger.info(
            "parquet refresh enabled: every %.1fh -> blob container=%s prefix=%s kinds=%s",
            parquet_refresh_hours, parquet_blob_container, parquet_blob_prefix or "(none)",
            parquet_kinds,
        )
    else:
        logger.info("parquet refresh disabled")

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        rd.ping()
    except Exception as exc:  # noqa: BLE001
        logger.error("redis ping failed: %s", exc)
        return 3
    logger.info(
        "connected to redis; symbols=%s indicators=%s poll=%ds funding_poll=%ds "
        "trim=%dh backfill=%dh gapfill_every=%ds gapfill_window=%dh",
        symbols, enabled, poll_seconds, funding_poll_seconds, trim_hours,
        backfill_hours, gapfill_interval, gapfill_window_hours,
    )

    with httpx.Client() as http:
        # 1) Initial backfill (paginated to cover the full retention window).
        for ind in enabled:
            # ``MFP_BACKFILL_HOURS`` is global; per-indicator legacy default
            # is kept only as a floor for funding (8h cadence → short windows
            # were historically OK) but trim_hours wins when larger.
            hours = max(
                backfill_hours,
                INDICATORS[ind].get("backfill_hours_default", trim_hours),
            )
            for sym in symbols:
                try:
                    backfill_initial(rd, http, ind, sym, hours=hours)
                except Exception as exc:  # noqa: BLE001
                    logger.error("backfill failed %s/%s: %s", ind, sym, exc)
                try:
                    pct, have, total = coverage_pct(
                        rd, ind, sym, hours=coverage_log_hours,
                    )
                    logger.info(
                        "coverage %s/%s last_%dh: %.1f%% (%d/%d)",
                        ind, sym, coverage_log_hours, pct * 100.0, have, total,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "coverage check failed %s/%s: %s", ind, sym, exc,
                    )

        # 2) Per-indicator next-run schedule. Funding is on its own slower cadence.
        now = time.time()
        next_run: dict[str, float] = {}
        for ind in enabled:
            next_run[ind] = now + (
                funding_poll_seconds if ind == "funding" else poll_seconds
            )

        # Schedule first parquet refresh ~5 min after startup so we don't
        # block the initial Redis backfill, then every refresh_hours.
        next_parquet_refresh = (
            time.time() + 300.0 if parquet_refresh_enabled else float("inf")
        )
        next_gapfill = time.time() + gapfill_interval

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

            # Periodic gap-fill across all enabled indicators.
            if time.time() >= next_gapfill:
                for ind in enabled:
                    for sym in symbols:
                        try:
                            n_gap = gap_fill(
                                rd, http, ind, sym,
                                window_hours=gapfill_window_hours,
                                trim_hours=trim_hours,
                            )
                            if n_gap:
                                logger.info(
                                    "gap-fill %s/%s: re-fetched=%d rows",
                                    ind, sym, n_gap,
                                )
                        except Exception as exc:  # noqa: BLE001
                            logger.error(
                                "gap-fill failed %s/%s: %s", ind, sym, exc,
                            )
                        try:
                            pct, have, total = coverage_pct(
                                rd, ind, sym, hours=coverage_log_hours,
                            )
                            logger.info(
                                "coverage %s/%s last_%dh: %.1f%% (%d/%d)",
                                ind, sym, coverage_log_hours,
                                pct * 100.0, have, total,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "coverage check failed %s/%s: %s", ind, sym, exc,
                            )
                next_gapfill = time.time() + gapfill_interval

            # Backtest parquet refresh (best-effort; failures never abort poll).
            if parquet_refresh_enabled and time.time() >= next_parquet_refresh:
                for sym in symbols:
                    blob_names: dict[str, str] = {}
                    for kind in parquet_kinds:
                        env_key = f"MFP_PARQUET_BLOB_NAME_{kind.upper()}_{sym}"
                        explicit = os.environ.get(env_key, "").strip()
                        if explicit:
                            blob_names[kind] = explicit
                    try:
                        result = refresh_perp_meta_parquet(  # type: ignore[misc]
                            symbol=sym,
                            kinds=parquet_kinds,
                            blob_container_name=parquet_blob_container,
                            blob_prefix=parquet_blob_prefix or None,
                            blob_names=blob_names or None,
                        )
                        logger.info("parquet refresh ok %s: %s", sym, result)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("parquet refresh failed for %s: %s", sym, exc)
                next_parquet_refresh = time.time() + parquet_refresh_hours * 3600.0

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
