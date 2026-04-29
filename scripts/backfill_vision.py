"""Backfill BTCUSDT perp metadata from Binance Vision archive (data.binance.vision).

Downloads:
  - Monthly fundingRate (available since 2020-01)
  - Daily metrics (sum_open_interest, sum_taker_long_short_vol_ratio,
    sum_toptrader_long_short_ratio, count_long_short_ratio, ... since 2020-09-01)

Writes consolidated parquet:
  data/perp_meta/{symbol}_funding.parquet  (cols: funding_time, funding_rate)
  data/perp_meta/{symbol}_oi_5m.parquet    (cols: timestamp, sum_oi, sum_oi_value)
  data/perp_meta/{symbol}_lsr_5m.parquet   (cols: timestamp, count_long_short_ratio,
                                                    sum_toptrader_long_short_ratio,
                                                    count_toptrader_long_short_ratio)
  data/perp_meta/{symbol}_taker_5m.parquet (cols: timestamp, sum_taker_long_short_vol_ratio)

Usage:
  python -u scripts/backfill_vision.py --symbol BTCUSDT --start 2020-01 --end 2026-04
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "perp_meta"
DATA_DIR.mkdir(parents=True, exist_ok=True)

VISION = "https://data.binance.vision"


def month_range(start: str, end: str):
    """Inclusive month iterator (YYYY-MM)."""
    s = datetime.strptime(start, "%Y-%m").date().replace(day=1)
    e = datetime.strptime(end, "%Y-%m").date().replace(day=1)
    cur = s
    while cur <= e:
        yield cur.strftime("%Y-%m")
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)


def day_range(start: str, end: str):
    """Inclusive day iterator (YYYY-MM-DD)."""
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    cur = s
    while cur <= e:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def fetch_zip_csv(client: httpx.Client, url: str) -> pd.DataFrame | None:
    try:
        r = client.get(url, timeout=30.0)
    except Exception as e:
        return None
    if r.status_code == 404:
        return None
    r.raise_for_status()
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        name = zf.namelist()[0]
        with zf.open(name) as f:
            return pd.read_csv(f)
    except Exception as e:
        print(f"  parse fail {url}: {e}", file=sys.stderr)
        return None


def backfill_funding(symbol: str, start_month: str, end_month: str) -> pd.DataFrame:
    months = list(month_range(start_month, end_month))
    print(f"[funding] {symbol}: {len(months)} months ({start_month}..{end_month})")
    urls = [
        (m, f"{VISION}/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip")
        for m in months
    ]
    frames: list[pd.DataFrame] = []
    n_ok = n_miss = 0
    with httpx.Client(http2=False, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {ex.submit(fetch_zip_csv, client, u): m for (m, u) in urls}
            for fut in as_completed(futs):
                m = futs[fut]
                df = fut.result()
                if df is None:
                    n_miss += 1
                    continue
                # Normalize columns
                if "calc_time" in df.columns:
                    df = df.rename(columns={"calc_time": "funding_time",
                                            "last_funding_rate": "funding_rate"})
                # Keep only needed
                keep = [c for c in ("funding_time", "funding_rate") if c in df.columns]
                df = df[keep]
                df["funding_time"] = df["funding_time"].astype("int64")
                df["funding_rate"] = df["funding_rate"].astype("float64")
                frames.append(df)
                n_ok += 1
    print(f"  ok={n_ok}  miss={n_miss}")
    if not frames:
        return pd.DataFrame(columns=["funding_time", "funding_rate"])
    out = pd.concat(frames, ignore_index=True).drop_duplicates("funding_time").sort_values("funding_time").reset_index(drop=True)
    return out


def backfill_metrics(symbol: str, start_day: str, end_day: str):
    days = list(day_range(start_day, end_day))
    print(f"[metrics] {symbol}: {len(days)} days ({start_day}..{end_day})")
    urls = [
        (d, f"{VISION}/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{d}.zip")
        for d in days
    ]
    frames: list[pd.DataFrame] = []
    n_ok = n_miss = 0
    with httpx.Client(http2=False, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = {ex.submit(fetch_zip_csv, client, u): d for (d, u) in urls}
            done = 0
            for fut in as_completed(futs):
                done += 1
                d = futs[fut]
                df = fut.result()
                if df is None:
                    n_miss += 1
                    if done % 200 == 0:
                        print(f"  ...{done}/{len(days)}  ok={n_ok} miss={n_miss}")
                    continue
                # create_time is "2025-06-15 00:05:00" UTC; convert to ms epoch
                ts_ns = pd.to_datetime(df["create_time"], utc=True).values.astype("datetime64[ns]").astype("int64")
                df["timestamp"] = (ts_ns // 1_000_000).astype("int64")
                frames.append(df)
                n_ok += 1
                if done % 200 == 0:
                    print(f"  ...{done}/{len(days)}  ok={n_ok} miss={n_miss}")
    print(f"  ok={n_ok}  miss={n_miss}")
    if not frames:
        return None, None, None
    full = pd.concat(frames, ignore_index=True).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

    oi = full[["timestamp", "sum_open_interest", "sum_open_interest_value"]].rename(
        columns={"sum_open_interest": "sum_oi", "sum_open_interest_value": "sum_oi_value"})
    lsr_cols = ["timestamp",
                "count_long_short_ratio",
                "sum_toptrader_long_short_ratio",
                "count_toptrader_long_short_ratio"]
    lsr_cols = [c for c in lsr_cols if c in full.columns]
    lsr = full[lsr_cols]
    taker = full[["timestamp", "sum_taker_long_short_vol_ratio"]]
    return oi, lsr, taker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--funding-start", default="2020-01", help="YYYY-MM")
    ap.add_argument("--funding-end", default=None, help="YYYY-MM (default: this month-1)")
    ap.add_argument("--metrics-start", default="2020-09-01")
    ap.add_argument("--metrics-end", default=None, help="YYYY-MM-DD (default: today-1)")
    ap.add_argument("--skip-funding", action="store_true")
    ap.add_argument("--skip-metrics", action="store_true")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    if args.funding_end is None:
        # last completed month
        first_of_month = today.replace(day=1)
        last_month = first_of_month - timedelta(days=1)
        args.funding_end = last_month.strftime("%Y-%m")
    if args.metrics_end is None:
        args.metrics_end = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    if not args.skip_funding:
        df_f = backfill_funding(args.symbol, args.funding_start, args.funding_end)
        out = DATA_DIR / f"{args.symbol}_funding.parquet"
        df_f.to_parquet(out, index=False)
        if len(df_f):
            t0 = datetime.fromtimestamp(df_f["funding_time"].iloc[0]/1000, tz=timezone.utc)
            t1 = datetime.fromtimestamp(df_f["funding_time"].iloc[-1]/1000, tz=timezone.utc)
            print(f"  -> {out}  ({len(df_f):,} rows, {t0:%Y-%m-%d}..{t1:%Y-%m-%d})")

    if not args.skip_metrics:
        oi, lsr, taker = backfill_metrics(args.symbol, args.metrics_start, args.metrics_end)
        if oi is not None:
            for name, df in (("oi", oi), ("lsr", lsr), ("taker", taker)):
                out = DATA_DIR / f"{args.symbol}_{name}_5m.parquet"
                df.to_parquet(out, index=False)
                t0 = datetime.fromtimestamp(df["timestamp"].iloc[0]/1000, tz=timezone.utc)
                t1 = datetime.fromtimestamp(df["timestamp"].iloc[-1]/1000, tz=timezone.utc)
                print(f"  -> {out}  ({len(df):,} rows, {t0:%Y-%m-%d}..{t1:%Y-%m-%d})")


if __name__ == "__main__":
    sys.exit(main())
