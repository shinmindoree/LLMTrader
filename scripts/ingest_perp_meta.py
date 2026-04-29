"""Binance USDM perpetual market-microstructure metadata backfiller.

Fetches funding rate, open interest history, top long/short account ratio,
and taker long/short ratio from the public Binance Futures REST API and
persists them as Parquet files under ``data/perp_meta/``.

Usage::

    python -u scripts/ingest_perp_meta.py \
        --symbol BTCUSDT \
        --start 2025-04-29 --end 2026-04-29 \
        --metrics funding,oi,lsr,taker \
        --period 5m

Notes
-----
- ``fundingRate`` history is available for the entire contract life.
- ``openInterestHist``, ``topLongShortAccountRatio`` and
  ``takerlongshortRatio`` are limited by Binance to the **most recent
  30 days**.  Requests outside that window will return empty.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

BINANCE_FAPI = "https://fapi.binance.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "perp_meta"


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


_PERIOD_TO_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


def _chunked_get(client: httpx.Client, path: str, base_params: dict[str, Any],
                 start_ms: int, end_ms: int, *, time_key: str = "fundingTime",
                 hard_limit: int = 1000, period_ms: int | None = None) -> list[dict]:
    """Generic paginated GET helper.

    The Binance ``/futures/data/*`` endpoints cap responses at 500 rows and
    silently return only the most recent slice when the requested window
    contains more.  We therefore walk forward in fixed-size sub-windows of
    ``hard_limit * period_ms`` milliseconds.  For endpoints without a
    period (e.g. fundingRate) we fall back to advancing by the last
    observed timestamp.
    """
    out: list[dict] = []
    seen_ts: set[int] = set()
    chunk_ms = (hard_limit - 1) * period_ms if period_ms else None
    cur = start_ms
    iter_count = 0
    while cur < end_ms:
        iter_count += 1
        if iter_count > 5000:
            print("  abort: iteration limit reached")
            break
        params = dict(base_params)
        params["startTime"] = cur
        if chunk_ms is not None:
            params["endTime"] = min(cur + chunk_ms, end_ms)
        else:
            params["endTime"] = end_ms
        params["limit"] = hard_limit
        for attempt in range(5):
            try:
                resp = client.get(f"{BINANCE_FAPI}{path}", params=params, timeout=30.0)
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                break
            except httpx.HTTPError as exc:  # noqa: PERF203
                if attempt == 4:
                    raise
                print(f"  retry {attempt + 1} after error: {exc}")
                time.sleep(2 ** attempt)
        rows = resp.json()
        if rows:
            new_rows = []
            last_ts = cur
            for r in rows:
                ts = int(r.get(time_key) or r.get("timestamp"))
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                new_rows.append(r)
                if ts > last_ts:
                    last_ts = ts
            out.extend(new_rows)
            advance = last_ts + 1 if last_ts > cur else cur + 1
        else:
            advance = cur + (chunk_ms or 60_000)
        # advance by whichever is larger (sub-window or last_ts+1)
        if chunk_ms is not None:
            cur = max(cur + chunk_ms + 1, advance)
        else:
            cur = advance
        if iter_count % 20 == 0:
            print(f"  ... fetched {len(out):,} rows, cur={cur}")
        time.sleep(0.15)
    return out


def fetch_funding(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows = _chunked_get(
        client,
        "/fapi/v1/fundingRate",
        {"symbol": symbol},
        start_ms,
        end_ms,
        time_key="fundingTime",
    )
    if not rows:
        return pd.DataFrame(columns=["funding_time", "funding_rate"])
    df = pd.DataFrame(rows)
    df["funding_time"] = df["fundingTime"].astype("int64")
    df["funding_rate"] = df["fundingRate"].astype(float)
    df = df[["funding_time", "funding_rate"]].drop_duplicates("funding_time").sort_values("funding_time")
    return df.reset_index(drop=True)


def fetch_oi_hist(client: httpx.Client, symbol: str, start_ms: int, end_ms: int,
                  period: str) -> pd.DataFrame:
    rows = _chunked_get(
        client,
        "/futures/data/openInterestHist",
        {"symbol": symbol, "period": period},
        start_ms,
        end_ms,
        time_key="timestamp",
        hard_limit=500,
        period_ms=_PERIOD_TO_MS[period],
    )
    if not rows:
        return pd.DataFrame(columns=["timestamp", "sum_oi", "sum_oi_value"])
    df = pd.DataFrame(rows)
    df["timestamp"] = df["timestamp"].astype("int64")
    df["sum_oi"] = df["sumOpenInterest"].astype(float)
    df["sum_oi_value"] = df["sumOpenInterestValue"].astype(float)
    return df[["timestamp", "sum_oi", "sum_oi_value"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def fetch_lsr(client: httpx.Client, symbol: str, start_ms: int, end_ms: int,
              period: str) -> pd.DataFrame:
    rows = _chunked_get(
        client,
        "/futures/data/topLongShortAccountRatio",
        {"symbol": symbol, "period": period},
        start_ms,
        end_ms,
        time_key="timestamp",
        hard_limit=500,
        period_ms=_PERIOD_TO_MS[period],
    )
    if not rows:
        return pd.DataFrame(columns=["timestamp", "long_account", "short_account", "long_short_ratio"])
    df = pd.DataFrame(rows)
    df["timestamp"] = df["timestamp"].astype("int64")
    df["long_account"] = df["longAccount"].astype(float)
    df["short_account"] = df["shortAccount"].astype(float)
    df["long_short_ratio"] = df["longShortRatio"].astype(float)
    return df[["timestamp", "long_account", "short_account", "long_short_ratio"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def fetch_taker(client: httpx.Client, symbol: str, start_ms: int, end_ms: int,
                period: str) -> pd.DataFrame:
    rows = _chunked_get(
        client,
        "/futures/data/takerlongshortRatio",
        {"symbol": symbol, "period": period},
        start_ms,
        end_ms,
        time_key="timestamp",
        hard_limit=500,
        period_ms=_PERIOD_TO_MS[period],
    )
    if not rows:
        return pd.DataFrame(columns=["timestamp", "buy_sell_ratio", "buy_vol", "sell_vol"])
    df = pd.DataFrame(rows)
    df["timestamp"] = df["timestamp"].astype("int64")
    df["buy_sell_ratio"] = df["buySellRatio"].astype(float)
    df["buy_vol"] = df["buyVol"].astype(float)
    df["sell_vol"] = df["sellVol"].astype(float)
    return df[["timestamp", "buy_sell_ratio", "buy_vol", "sell_vol"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    print(f"  -> {path}  ({len(df):,} rows)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD UTC")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD UTC (exclusive end-of-day)")
    parser.add_argument(
        "--metrics",
        default="funding,oi,lsr,taker",
        help="comma-separated subset of {funding,oi,lsr,taker}",
    )
    parser.add_argument("--period", default="5m",
                        help="period for OI/LSR/taker (5m,15m,30m,1h,2h,4h,6h,12h,1d)")
    args = parser.parse_args()

    start_ms = _to_ms(args.start)
    end_ms = _to_ms(args.end)
    metrics = {m.strip() for m in args.metrics.split(",") if m.strip()}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(headers={"User-Agent": "llmtrader-ingest/1.0"})
    try:
        if "funding" in metrics:
            print(f"[funding] {args.symbol} {args.start}..{args.end}")
            df = fetch_funding(client, args.symbol, start_ms, end_ms)
            _save(df, DATA_DIR / f"{args.symbol}_funding.parquet")
        if "oi" in metrics:
            print(f"[oi {args.period}] {args.symbol} {args.start}..{args.end}")
            df = fetch_oi_hist(client, args.symbol, start_ms, end_ms, args.period)
            _save(df, DATA_DIR / f"{args.symbol}_oi_{args.period}.parquet")
        if "lsr" in metrics:
            print(f"[lsr {args.period}] {args.symbol} {args.start}..{args.end}")
            df = fetch_lsr(client, args.symbol, start_ms, end_ms, args.period)
            _save(df, DATA_DIR / f"{args.symbol}_lsr_{args.period}.parquet")
        if "taker" in metrics:
            print(f"[taker {args.period}] {args.symbol} {args.start}..{args.end}")
            df = fetch_taker(client, args.symbol, start_ms, end_ms, args.period)
            _save(df, DATA_DIR / f"{args.symbol}_taker_{args.period}.parquet")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
