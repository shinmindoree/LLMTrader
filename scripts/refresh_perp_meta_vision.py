"""Incrementally refresh perp-meta parquets from Binance Vision archives.

Fills the gap between the existing ``data/perp_meta/<SYMBOL>_*.parquet`` files
and the latest data available on data.binance.vision. Covers:

  - <SYMBOL>_funding.parquet      (monthly fundingRate archives)
  - <SYMBOL>_lsr_5m.parquet       (daily metrics archive)
  - <SYMBOL>_taker_5m.parquet     (daily metrics archive)
  - <SYMBOL>_oi_5m.parquet        (optional; daily metrics archive)

Schemas match ``backfill_vision.py`` exactly (so downstream consumers in
``multi_factor_portfolio_strategy.py`` and ``alpha_lab/dataset.py`` keep
working).

Usage::

    python -u scripts/refresh_perp_meta_vision.py --symbol BTCUSDT
    python -u scripts/refresh_perp_meta_vision.py --symbol BTCUSDT --metrics funding,lsr,taker

Notes
-----
- Daily Vision archives appear with a 1-2 day lag, so the script fetches up
  to ``today - 1`` (UTC) by default and tolerates 404s for not-yet-published
  days.
- Existing rows are preserved; new rows are concatenated, deduplicated on the
  timestamp key, sorted, and written back atomically.
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
VISION = "https://data.binance.vision"

ALL_METRICS = ("funding", "oi", "lsr", "taker")


# ---------------------------------------------------------------------------
# Vision fetch helpers (mirror backfill_vision.py)
# ---------------------------------------------------------------------------
def _fetch_zip_csv(client: httpx.Client, url: str) -> pd.DataFrame | None:
    try:
        r = client.get(url, timeout=30.0)
    except Exception:
        return None
    if r.status_code == 404:
        return None
    try:
        r.raise_for_status()
    except Exception as exc:
        print(f"  http {r.status_code} {url}: {exc}", file=sys.stderr)
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        name = zf.namelist()[0]
        with zf.open(name) as f:
            return pd.read_csv(f)
    except Exception as exc:
        print(f"  parse fail {url}: {exc}", file=sys.stderr)
        return None


def _month_range(start: date, end: date):
    """Inclusive month iterator on first-of-month boundaries."""
    cur = start.replace(day=1)
    last = end.replace(day=1)
    while cur <= last:
        yield cur.strftime("%Y-%m")
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)


def _day_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


# ---------------------------------------------------------------------------
# Funding (monthly archives)
# ---------------------------------------------------------------------------
def _refresh_funding(symbol: str, end_today: date) -> None:
    path = DATA_DIR / f"{symbol}_funding.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run scripts/backfill_vision.py first"
        )
    existing = pd.read_parquet(path)
    last_ms = int(existing["funding_time"].max()) if len(existing) else 0
    last_dt = (
        datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
        if last_ms
        else date(2020, 1, 1)
    )
    # Re-fetch from the month of last_dt (cheap, dedupe handles overlap).
    months = list(_month_range(last_dt, end_today))
    if not months:
        print(f"[funding] up-to-date (last={last_dt})")
        return
    print(
        f"[funding] {symbol}: last_ts={last_dt} -> {end_today.strftime('%Y-%m')}, "
        f"{len(months)} months to refetch"
    )
    urls = [
        (
            m,
            f"{VISION}/data/futures/um/monthly/fundingRate/{symbol}/"
            f"{symbol}-fundingRate-{m}.zip",
        )
        for m in months
    ]
    frames: list[pd.DataFrame] = []
    n_ok = n_miss = 0
    with httpx.Client(http2=False, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_fetch_zip_csv, client, u): m for (m, u) in urls}
            for fut in as_completed(futs):
                df = fut.result()
                if df is None:
                    n_miss += 1
                    continue
                if "calc_time" in df.columns:
                    df = df.rename(
                        columns={
                            "calc_time": "funding_time",
                            "last_funding_rate": "funding_rate",
                        }
                    )
                keep = [c for c in ("funding_time", "funding_rate") if c in df.columns]
                df = df[keep]
                df["funding_time"] = df["funding_time"].astype("int64")
                df["funding_rate"] = df["funding_rate"].astype("float64")
                frames.append(df)
                n_ok += 1
    print(f"  vision ok={n_ok}  miss={n_miss}")
    if not frames:
        print("  no new rows")
        return
    new_df = pd.concat(frames, ignore_index=True)
    merged = (
        pd.concat([existing, new_df], ignore_index=True)
        .drop_duplicates("funding_time", keep="last")
        .sort_values("funding_time")
        .reset_index(drop=True)
    )
    appended = len(merged) - len(existing)
    merged.to_parquet(path, index=False)
    t0 = datetime.fromtimestamp(int(merged["funding_time"].iloc[0]) / 1000, tz=timezone.utc)
    t1 = datetime.fromtimestamp(int(merged["funding_time"].iloc[-1]) / 1000, tz=timezone.utc)
    print(
        f"  -> {path}  (+{appended} rows, total {len(merged):,}, "
        f"{t0:%Y-%m-%d}..{t1:%Y-%m-%d %H:%M})"
    )


# ---------------------------------------------------------------------------
# Daily metrics (oi / lsr / taker share the same daily zip)
# ---------------------------------------------------------------------------
def _read_existing(path: Path, ts_col: str = "timestamp") -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=[ts_col])


def _refresh_metrics(symbol: str, kinds: set[str], end_today: date) -> None:
    """Refresh oi/lsr/taker from daily metrics archives."""
    targets = {
        "oi": DATA_DIR / f"{symbol}_oi_5m.parquet",
        "lsr": DATA_DIR / f"{symbol}_lsr_5m.parquet",
        "taker": DATA_DIR / f"{symbol}_taker_5m.parquet",
    }
    targets = {k: v for k, v in targets.items() if k in kinds}
    if not targets:
        return

    # Determine earliest "last_ts" across requested files (overlap day for safety).
    last_dts: dict[str, date] = {}
    for k, p in targets.items():
        df = _read_existing(p)
        if len(df):
            last_ms = int(df["timestamp"].max())
            last_dts[k] = datetime.fromtimestamp(
                last_ms / 1000, tz=timezone.utc
            ).date()
        else:
            last_dts[k] = date(2020, 9, 1)
            print(f"  [{k}] empty parquet, seeding from 2020-09-01")

    # Re-fetch from the earliest last_dt onward (one overlap day = idempotent).
    fetch_start = min(last_dts.values())
    if fetch_start >= end_today:
        print(
            f"[metrics] up-to-date (oldest last_ts={fetch_start} >= today-1={end_today})"
        )
        return

    days = list(_day_range(fetch_start, end_today))
    print(
        f"[metrics] {symbol}: refetching {len(days)} days "
        f"({fetch_start}..{end_today}) for kinds={sorted(kinds)}"
    )
    for k, d in last_dts.items():
        print(f"   {k}: existing last_day={d}")

    urls = [
        (d, f"{VISION}/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{d}.zip")
        for d in days
    ]
    frames: list[pd.DataFrame] = []
    n_ok = n_miss = 0
    with httpx.Client(http2=False, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = {ex.submit(_fetch_zip_csv, client, u): d for (d, u) in urls}
            done = 0
            for fut in as_completed(futs):
                done += 1
                df = fut.result()
                if df is None:
                    n_miss += 1
                    continue
                ts_ns = (
                    pd.to_datetime(df["create_time"], utc=True)
                    .values.astype("datetime64[ns]")
                    .astype("int64")
                )
                df["timestamp"] = (ts_ns // 1_000_000).astype("int64")
                frames.append(df)
                n_ok += 1
                if done % 50 == 0:
                    print(f"  ...{done}/{len(days)}  ok={n_ok} miss={n_miss}")
    print(f"  vision ok={n_ok}  miss={n_miss}")
    if not frames:
        print("  no new daily archives available yet")
        return

    full = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # Per-kind merge
    if "oi" in kinds:
        new_df = full[["timestamp", "sum_open_interest", "sum_open_interest_value"]].rename(
            columns={
                "sum_open_interest": "sum_oi",
                "sum_open_interest_value": "sum_oi_value",
            }
        )
        _merge_and_write(targets["oi"], new_df, key="timestamp")
    if "lsr" in kinds:
        lsr_cols = [
            c
            for c in (
                "timestamp",
                "count_long_short_ratio",
                "sum_toptrader_long_short_ratio",
                "count_toptrader_long_short_ratio",
            )
            if c in full.columns
        ]
        new_df = full[lsr_cols]
        _merge_and_write(targets["lsr"], new_df, key="timestamp")
    if "taker" in kinds:
        new_df = full[["timestamp", "sum_taker_long_short_vol_ratio"]]
        _merge_and_write(targets["taker"], new_df, key="timestamp")


def _merge_and_write(path: Path, new_df: pd.DataFrame, *, key: str) -> None:
    if path.exists():
        existing = pd.read_parquet(path)
    else:
        existing = pd.DataFrame(columns=new_df.columns)
    before = len(existing)
    merged = (
        pd.concat([existing, new_df], ignore_index=True)
        .drop_duplicates(key, keep="last")
        .sort_values(key)
        .reset_index(drop=True)
    )
    appended = len(merged) - before
    merged.to_parquet(path, index=False)
    if len(merged):
        t0 = datetime.fromtimestamp(int(merged[key].iloc[0]) / 1000, tz=timezone.utc)
        t1 = datetime.fromtimestamp(int(merged[key].iloc[-1]) / 1000, tz=timezone.utc)
        print(
            f"  -> {path.name}  (+{appended} rows, total {len(merged):,}, "
            f"{t0:%Y-%m-%d}..{t1:%Y-%m-%d %H:%M})"
        )
    else:
        print(f"  -> {path.name}  (empty)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument(
        "--metrics",
        default="funding,lsr,taker",
        help=f"comma-separated subset of {ALL_METRICS} (default: funding,lsr,taker; "
        "OI is normally refreshed via refresh_oi_parquet.py)",
    )
    ap.add_argument(
        "--end",
        default=None,
        help="YYYY-MM-DD UTC inclusive (default: today-1, since same-day Vision "
        "archives are not yet published)",
    )
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    end_today = (
        datetime.strptime(args.end, "%Y-%m-%d").date()
        if args.end
        else today - timedelta(days=1)
    )
    kinds = {m.strip() for m in args.metrics.split(",") if m.strip()}
    bad = kinds - set(ALL_METRICS)
    if bad:
        print(f"unknown metrics: {sorted(bad)} (allowed: {ALL_METRICS})", file=sys.stderr)
        return 2

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if "funding" in kinds:
        _refresh_funding(args.symbol, end_today)
    metric_kinds = kinds & {"oi", "lsr", "taker"}
    if metric_kinds:
        _refresh_metrics(args.symbol, metric_kinds, end_today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
