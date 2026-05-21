"""Quick diagnostic: print ts range + row count of each MFP parquet source."""
import os
import pandas as pd

PAIRS = [
    ("BTCUSDT_15m_klines", "ts"),
    ("BTCUSDT_oi_5m", "timestamp"),
    ("BTCUSDT_funding", "funding_time"),
    ("BTCUSDT_taker_5m", "timestamp"),
    ("BTCUSDT_lsr_5m", "timestamp"),
]
for name, col in PAIRS:
    p = f"data/perp_meta/{name}.parquet"
    if not os.path.exists(p):
        print(name, "MISSING")
        continue
    df = pd.read_parquet(p)
    mn = pd.Timestamp(int(df[col].min()), unit="ms", tz="UTC")
    mx = pd.Timestamp(int(df[col].max()), unit="ms", tz="UTC")
    print(f"{name}: rows={len(df)} min={mn} max={mx}")
