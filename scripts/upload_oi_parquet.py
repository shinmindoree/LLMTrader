"""Upload OI parquet files to Azure Blob Storage so deployed runners can
download them at backtest time. Re-run after refreshing the parquet locally
via `scripts/ingest_perp_meta.py`.

Usage:
    # one-time: create the container (if not already)
    az storage container create --account-name <storage> --name market-data

    # upload BTCUSDT
    $env:AZURE_BLOB_CONNECTION_STRING = "..."
    python scripts/upload_oi_parquet.py --symbols BTCUSDT --container market-data

Then on the runner Container App, set:
    OI_PARQUET_BLOB_CONTAINER=market-data
    OI_PARQUET_BLOB_NAME_BTCUSDT=perp_meta/BTCUSDT_oi_5m.parquet
    AZURE_BLOB_CONNECTION_STRING=<same as web/api>   (or AZURE_BLOB_ACCOUNT_URL + managed identity)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTCUSDT", help="comma-separated")
    parser.add_argument("--container", default="market-data")
    parser.add_argument("--prefix", default="perp_meta",
                        help="blob path prefix; final name = {prefix}/{symbol}_oi_5m.parquet")
    parser.add_argument("--data-dir", default="data/perp_meta",
                        help="local directory containing the parquet files")
    args = parser.parse_args()

    from azure.storage.blob import ContainerClient
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "").strip()
    if not conn_str:
        print("ERROR: set AZURE_BLOB_CONNECTION_STRING", file=sys.stderr)
        return 2
    client = ContainerClient.from_connection_string(conn_str, args.container)
    try:
        client.create_container()
    except Exception:  # already exists
        pass

    repo_root = Path(__file__).resolve().parents[1]
    data_dir = (repo_root / args.data_dir).resolve()
    for sym in (s.strip().upper() for s in args.symbols.split(",") if s.strip()):
        local = data_dir / f"{sym}_oi_5m.parquet"
        if not local.exists():
            print(f"SKIP {sym}: missing {local}")
            continue
        blob_path = f"{args.prefix.rstrip('/')}/{sym}_oi_5m.parquet"
        print(f"uploading {local} -> {args.container}/{blob_path}")
        with local.open("rb") as f:
            client.upload_blob(blob_path, f, overwrite=True)
        print(f"  done ({local.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
