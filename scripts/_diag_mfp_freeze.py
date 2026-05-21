"""Reproduce MFP backtest hypothesis: when MFP parquet ends earlier than the
engine-fed klines, _tf_idx_for returns -1 for every leg on later bars and the
leg state freezes — so no trades after the parquet cutoff.

Reads local data/perp_meta/*.parquet (which currently end at 5/10 09:00 UTC),
builds the MFP unified dataset, then synthesises bar timestamps from 5/10 to
5/21 UTC and counts how many of those bars produce a non-(-1) tf_idx for ANY
leg. The output should show:

  - bars up to parquet last_ts: every-bar tf_idx hits across leg TFs
  - bars after parquet last_ts: 0 leg hits for ALL bars (== complete freeze)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

# Avoid pulling Redis or live providers; this script is read-only.
import strategies.multi_factor_portfolio_strategy as mfp

unified = mfp._load_unified_dataset("BTCUSDT")
last_parquet_ts = int(unified["ts"].iloc[-1])
print(f"unified rows={len(unified)} last_ts={pd.Timestamp(last_parquet_ts, unit='ms', tz='UTC')}")

legs = [mfp._LegState(leg, unified) for leg in mfp.ALL_LEGS]
strat = mfp.MultiFactorPortfolioStrategy()
strat._legs = legs

# Simulate engine feeding 15m bars from 5/10 UTC to 5/21 UTC.
start_ts = int(pd.Timestamp("2026-05-10 00:00:00", tz="UTC").timestamp() * 1000)
end_ts = int(pd.Timestamp("2026-05-21 23:45:00", tz="UTC").timestamp() * 1000)
bar_ms = 15 * 60 * 1000

print()
print(f"{'bar_ts (UTC)':30s}  {'<=parquet?':12s}  any_leg_hit  legs_w_hit")
hit_counts = {"<=parquet": [0, 0], ">parquet": [0, 0]}
ts = start_ts
sample_after = 0
while ts <= end_ts:
    hits = 0
    for leg in legs:
        if strat._tf_idx_for(leg, ts) >= 0:
            hits += 1
    bucket = "<=parquet" if ts <= last_parquet_ts else ">parquet"
    hit_counts[bucket][0] += 1
    if hits > 0:
        hit_counts[bucket][1] += 1
    # Print a few representative samples each side of the cutoff.
    if (ts <= last_parquet_ts and ts % (4 * 3600 * 1000) == 0) or (ts > last_parquet_ts and sample_after < 8 and ts % (3600 * 1000) == 0):
        if ts > last_parquet_ts:
            sample_after += 1
        ts_iso = str(pd.Timestamp(ts, unit="ms", tz="UTC"))
        any_hit = "yes" if hits > 0 else "no"
        print(f"{ts_iso:30s}  {bucket:12s}  {any_hit:11s}  {hits}")
    ts += bar_ms

print()
print("summary:")
for bucket, (total, with_hit) in hit_counts.items():
    print(f"  bars {bucket}: total={total} bars_with_leg_hit={with_hit}")
