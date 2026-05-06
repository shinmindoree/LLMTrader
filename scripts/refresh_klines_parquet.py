"""Refresh BTCUSDT klines parquet from Binance USDT-M Futures HTTP fapi.

Pulls /fapi/v1/klines paginated and writes a clean parquet at
``data/perp_meta/{SYMBOL}_{INTERVAL}_klines.parquet`` with columns
``ts, o, h, l, c`` (same schema as the existing file, but produced from
the authoritative fapi source instead of a possibly corrupted backfill).

If a file already exists at the destination, it is renamed to
``...klines.parquet.corrupt-YYYYMMDD-HHMMSS`` before the new data is
written. This is a one-shot tool, not an incremental updater.

Usage:
    python scripts/refresh_klines_parquet.py --symbol BTCUSDT --interval 15m \\
        --start 2020-09-01 --end 2026-04-29
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "perp_meta"
DATA_DIR.mkdir(parents=True, exist_ok=True)

FAPI = "https://fapi.binance.com"
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
}


def fetch_chunk(
    session: requests.Session,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1500,
) -> list[list]:
    """Single fapi /klines request (non-retried, callers handle retry)."""
    r = session.get(
        f"{FAPI}/fapi/v1/klines",
        params={
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
        },
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


def fetch_all(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    step = INTERVAL_MS[interval]
    rows: list[list] = []
    cursor = start_ms
    n_calls = 0
    last_print = time.time()
    with requests.Session() as session:
        while cursor <= end_ms:
            for attempt in range(5):
                try:
                    chunk = fetch_chunk(session, symbol, interval, cursor, end_ms, limit=1500)
                    break
                except requests.RequestException as e:
                    backoff = 1.5**attempt
                    print(f"  retry {attempt+1}/5 after {backoff:.1f}s ({e})", file=sys.stderr)
                    time.sleep(backoff)
            else:
                raise RuntimeError(f"failed after 5 retries at cursor={cursor}")
            n_calls += 1
            if not chunk:
                break
            rows.extend(chunk)
            last_open = int(chunk[-1][0])
            # Advance cursor past the last open we got. If we received <1500
            # rows we are done (server returned everything available up to
            # end_ms).
            cursor = last_open + step
            if len(chunk) < 1500:
                break
            # Light rate-limit courtesy.
            if n_calls % 10 == 0:
                time.sleep(0.05)
            if time.time() - last_print > 5.0:
                last_dt = datetime.fromtimestamp(last_open / 1000, tz=timezone.utc)
                print(f"  ...{n_calls} calls, {len(rows):,} bars, last {last_dt:%Y-%m-%d %H:%M}")
                last_print = time.time()
    print(f"  total {n_calls} HTTP calls, {len(rows):,} raw bars")
    if not rows:
        return pd.DataFrame(columns=["ts", "o", "h", "l", "c"])
    df = pd.DataFrame(rows, columns=[
        "ts", "o", "h", "l", "c", "v", "ct", "qv", "nt", "tb", "tq", "i",
    ])
    df = df[["ts", "o", "h", "l", "c"]]
    df["ts"] = df["ts"].astype("int64")
    for col in ("o", "h", "l", "c"):
        df[col] = df[col].astype("float64")
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="15m", choices=list(INTERVAL_MS.keys()))
    ap.add_argument("--start", default="2020-09-01", help="YYYY-MM-DD UTC inclusive")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD UTC inclusive (default: today-1)")
    ap.add_argument("--out", default=None, help="override output path")
    args = ap.parse_args()

    if args.end is None:
        today = datetime.now(timezone.utc).date()
        args.end = today.isoformat()

    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(args.end, tz="UTC").timestamp() * 1000) + 24 * 3600 * 1000 - 1

    out_path = Path(args.out) if args.out else (
        DATA_DIR / f"{args.symbol}_{args.interval}_klines.parquet"
    )

    print(f"[fapi klines] {args.symbol} {args.interval}  {args.start}..{args.end}")
    print(f"  -> {out_path}")

    if out_path.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = out_path.with_suffix(out_path.suffix + f".corrupt-{ts}")
        out_path.rename(backup)
        print(f"  backed up existing parquet to {backup.name}")

    df = fetch_all(args.symbol, args.interval, start_ms, end_ms)
    df.to_parquet(out_path, index=False)

    if len(df):
        t0 = datetime.fromtimestamp(int(df["ts"].iloc[0]) / 1000, tz=timezone.utc)
        t1 = datetime.fromtimestamp(int(df["ts"].iloc[-1]) / 1000, tz=timezone.utc)
        print(
            f"  wrote {len(df):,} bars, {t0:%Y-%m-%d %H:%M} .. {t1:%Y-%m-%d %H:%M} UTC"
        )
    else:
        print("  WARNING: no bars fetched")


if __name__ == "__main__":
    main()
