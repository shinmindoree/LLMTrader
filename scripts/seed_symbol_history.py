"""One-shot full-history seeder for a symbol's perp-meta parquet feeds.

Strategy-agnostic. Backfills the 5 canonical parquet feeds for ``<SYMBOL>``
from public Binance sources (Vision archive + fapi /klines) and optionally
uploads them to Azure Blob under the convention path
``<prefix>/<SYMBOL>_<feed>.parquet`` so that:

  - **cloud UI backtests** resolve full history (the providers read these
    blobs via the convention fallback), and
  - **live trading** seeds from the same blob parquet before gap-filling
    the recent window from Redis / Binance.

Feeds produced::

  <SYM>_15m_klines.parquet  (ts, o, h, l, c)                 <- fapi /klines
  <SYM>_oi_5m.parquet       (timestamp, sum_oi, sum_oi_value) <- Vision metrics
  <SYM>_taker_5m.parquet    (timestamp, sum_taker_long_short_vol_ratio)
  <SYM>_lsr_5m.parquet      (timestamp, count_long_short_ratio, ...)
  <SYM>_funding.parquet     (funding_time, funding_rate)      <- Vision funding

The blob schemas are byte-compatible with the live ingestors
(``refresh_oi_parquet.py`` / ``refresh_perp_meta_parquet.py``), which use a
download -> merge -> dedup-on-timestamp -> upload pattern. So overwriting a
blob with full history here is safe: the next ingestor refresh merges the
recent rows on top and **preserves** the full history going forward.

Where to run
------------
Uploading requires network access to the storage account (which has
``publicNetworkAccess=Disabled``) plus ``Storage Blob Data Contributor``.
The **backtest runner** container app satisfies both (VNet private endpoint +
system-assigned identity role). Run there via ``az containerapp exec``::

    python scripts/seed_symbol_history.py --symbol ETHUSDT

Locally (no VNet) use ``--skip-upload`` to only produce the local feeds under
``data/perp_meta/`` -- e.g. before ``scripts/discover_mfp_params.py``::

    python scripts/seed_symbol_history.py --symbol ETHUSDT --skip-upload
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backfill_vision import backfill_funding, backfill_metrics  # noqa: E402
from refresh_klines_parquet import fetch_all as fetch_klines  # noqa: E402

DATA_DIR = _ROOT / "data" / "perp_meta"

# feed key -> (filename suffix, timestamp column) for the canonical files.
_FEEDS = {
    "klines":  ("{symbol}_15m_klines.parquet", "ts"),
    "oi":      ("{symbol}_oi_5m.parquet", "timestamp"),
    "taker":   ("{symbol}_taker_5m.parquet", "timestamp"),
    "lsr":     ("{symbol}_lsr_5m.parquet", "timestamp"),
    "funding": ("{symbol}_funding.parquet", "funding_time"),
}


def _coverage(df: pd.DataFrame, ts_col: str) -> str:
    if df is None or len(df) == 0:
        return "EMPTY"
    unit = "ms"
    t = pd.to_datetime(df[ts_col], unit=unit, utc=True)
    return f"{len(df):,} rows  {t.min():%Y-%m-%d} .. {t.max():%Y-%m-%d}"


def build_feeds(
    symbol: str,
    *,
    klines_start: str,
    metrics_start: str,
    funding_start: str,
    klines_interval: str = "15m",
) -> dict[str, pd.DataFrame]:
    """Backfill all 5 feeds for ``symbol`` and return them keyed by feed name."""
    today = datetime.now(timezone.utc).date()
    end_day = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    first_of_month = today.replace(day=1)
    funding_end = (first_of_month - timedelta(days=1)).strftime("%Y-%m")

    feeds: dict[str, pd.DataFrame] = {}

    print(f"\n[seed] {symbol}: klines (fapi {klines_interval}) "
          f"{klines_start}..{end_day}")
    start_ms = int(pd.Timestamp(klines_start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end_day, tz="UTC").timestamp() * 1000) \
        + 24 * 3600 * 1000 - 1
    feeds["klines"] = fetch_klines(symbol, klines_interval, start_ms, end_ms)

    print(f"\n[seed] {symbol}: funding (Vision) {funding_start}..{funding_end}")
    feeds["funding"] = backfill_funding(symbol, funding_start, funding_end)

    print(f"\n[seed] {symbol}: metrics (Vision) {metrics_start}..{end_day}")
    oi, lsr, taker = backfill_metrics(symbol, metrics_start, end_day)
    feeds["oi"] = oi if oi is not None else pd.DataFrame()
    feeds["lsr"] = lsr if lsr is not None else pd.DataFrame()
    feeds["taker"] = taker if taker is not None else pd.DataFrame()
    return feeds


def write_local(symbol: str, feeds: dict[str, pd.DataFrame]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for key, (suffix_fmt, ts_col) in _FEEDS.items():
        df = feeds.get(key)
        if df is None or len(df) == 0:
            print(f"  [local] {key}: EMPTY -- skipped")
            continue
        out = DATA_DIR / suffix_fmt.format(symbol=symbol)
        df.to_parquet(out, index=False)
        print(f"  [local] {out.name}: {_coverage(df, ts_col)}")


def upload_blob(
    symbol: str,
    feeds: dict[str, pd.DataFrame],
    *,
    container_name: str,
    prefix: str,
) -> None:
    from refresh_perp_meta_parquet import _blob_container_client, _upload

    prefix = prefix.strip().rstrip("/")
    container = _blob_container_client(container_name)
    for key, (suffix_fmt, ts_col) in _FEEDS.items():
        df = feeds.get(key)
        if df is None or len(df) == 0:
            print(f"  [blob] {key}: EMPTY -- skipped")
            continue
        fname = suffix_fmt.format(symbol=symbol)
        blob_name = f"{prefix}/{fname}" if prefix else fname
        n = _upload(container, blob_name, df)
        print(f"  [blob] {container_name}/{blob_name}: "
              f"{_coverage(df, ts_col)}  ({n:,} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True, help="e.g. ETHUSDT")
    ap.add_argument("--klines-start", default="2020-09-01",
                    help="YYYY-MM-DD UTC inclusive (fapi clamps to listing date)")
    ap.add_argument("--metrics-start", default="2020-09-01",
                    help="YYYY-MM-DD UTC inclusive (Vision metrics earliest 2020-09-01)")
    ap.add_argument("--funding-start", default="2020-01",
                    help="YYYY-MM (Vision fundingRate earliest 2020-01)")
    ap.add_argument("--klines-interval", default="15m")
    ap.add_argument("--blob-container", default="market-data")
    ap.add_argument("--blob-prefix", default="perp_meta")
    ap.add_argument("--skip-upload", action="store_true",
                    help="only write local data/perp_meta/ files (no blob upload)")
    ap.add_argument("--skip-local", action="store_true",
                    help="do not write local files (upload only)")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    feeds = build_feeds(
        symbol,
        klines_start=args.klines_start,
        metrics_start=args.metrics_start,
        funding_start=args.funding_start,
        klines_interval=args.klines_interval,
    )

    print(f"\n[seed] {symbol} backfill summary:")
    for key, (_suffix, ts_col) in _FEEDS.items():
        print(f"  {key:8} {_coverage(feeds.get(key), ts_col)}")

    if not args.skip_local:
        print(f"\n[seed] writing local feeds -> {DATA_DIR}")
        write_local(symbol, feeds)

    if args.skip_upload:
        print("\n[seed] --skip-upload set; blob upload skipped.")
        return 0

    print(f"\n[seed] uploading to blob {args.blob_container}/{args.blob_prefix}/")
    upload_blob(symbol, feeds,
                container_name=args.blob_container,
                prefix=args.blob_prefix)
    print("\n[seed] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
