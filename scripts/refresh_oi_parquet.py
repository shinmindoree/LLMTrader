"""Incrementally refresh the OI 5m parquet used by backtests.

Reads the existing parquet from Azure Blob (or local), fetches new rows from
Binance ``/futures/data/openInterestHist?period=5m`` since the last stored
timestamp, appends + dedupes, then writes back. Designed to be called
periodically (default: from inside ``oi_ingestor.py`` once per 6 hours, but
also runnable as a one-shot CLI).

Auth (mirrors ``src/indicators/oi_provider.py::_download_blob``):
- ``AZURE_BLOB_CONNECTION_STRING`` (preferred), or
- ``AZURE_BLOB_ACCOUNT_URL`` + managed identity / Azure CLI fallback.

Binance constraint:
- ``openInterestHist`` only serves the **last 30 days**. As long as the
  parquet is refreshed at least every ~25 days, no gap will form.

Usage (CLI)::

    python scripts/refresh_oi_parquet.py \
        --symbol BTCUSDT \
        --blob-container market-data \
        --blob-name perp_meta/BTCUSDT_oi_5m.parquet
"""
from __future__ import annotations

import argparse
import io
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

import httpx

logger = logging.getLogger("refresh_oi_parquet")

BINANCE_FAPI_DEFAULT = "https://fapi.binance.com"
PERIOD_MS = 5 * 60 * 1000  # 5m
BINANCE_LIMIT = 500
BINANCE_MAX_LOOKBACK_MS = 30 * 24 * 3600 * 1000  # ~30 days hard cap


# ---------------------------------------------------------------------------
# Blob helpers
# ---------------------------------------------------------------------------
def _blob_container_client(container_name: str):
    """Build a ContainerClient using the same auth chain as oi_provider."""
    from azure.storage.blob import ContainerClient

    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "").strip()
    if conn_str:
        return ContainerClient.from_connection_string(conn_str, container_name)

    account_url = os.environ.get("AZURE_BLOB_ACCOUNT_URL", "").strip()
    if not account_url:
        raise RuntimeError(
            "Set AZURE_BLOB_CONNECTION_STRING or AZURE_BLOB_ACCOUNT_URL."
        )
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    if os.environ.get("IDENTITY_ENDPOINT"):
        kwargs: dict = {}
        if client_id:
            kwargs["client_id"] = client_id
        credential = ManagedIdentityCredential(**kwargs)
    else:
        kwargs = {}
        if client_id:
            kwargs["managed_identity_client_id"] = client_id
        credential = DefaultAzureCredential(**kwargs)
    return ContainerClient(
        account_url=account_url, container_name=container_name, credential=credential
    )


def _download_existing_parquet(container, blob_name: str):
    """Return (DataFrame, exists). When the blob is missing, returns empty df."""
    import pandas as pd

    blob = container.get_blob_client(blob_name)
    try:
        data = blob.download_blob().readall()
    except Exception as exc:  # noqa: BLE001
        # azure.core.exceptions.ResourceNotFoundError or similar
        logger.warning("blob %s not found (%s); will create new one", blob_name, exc)
        return pd.DataFrame(columns=["timestamp", "sum_oi", "sum_oi_value"]), False
    df = pd.read_parquet(io.BytesIO(data))
    return df.sort_values("timestamp").reset_index(drop=True), True


def _upload_parquet(container, blob_name: str, df) -> int:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    payload = buf.getvalue()
    container.get_blob_client(blob_name).upload_blob(payload, overwrite=True)
    return len(payload)


# ---------------------------------------------------------------------------
# Binance fetch
# ---------------------------------------------------------------------------
def _fetch_oi_5m_window(
    client: httpx.Client, symbol: str, start_ms: int, end_ms: int, *, base_url: str
) -> list[dict]:
    """Fetch a single window of openInterestHist?period=5m. Caller paginates."""
    params = {
        "symbol": symbol,
        "period": "5m",
        "limit": BINANCE_LIMIT,
        "startTime": int(start_ms),
        "endTime": int(end_ms),
    }
    for attempt in range(5):
        try:
            resp = client.get(
                f"{base_url}/futures/data/openInterestHist",
                params=params,
                timeout=20.0,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt
                logger.warning("oi fetch http=%s, sleep %ds", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() or []
        except httpx.HTTPError as exc:
            if attempt == 4:
                raise
            logger.warning("oi fetch error attempt=%d: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return []


def _fetch_oi_5m_range(
    client: httpx.Client, symbol: str, start_ms: int, end_ms: int, *, base_url: str
) -> list[dict]:
    """Paginate Binance ``openInterestHist`` across [start_ms, end_ms]."""
    if start_ms >= end_ms:
        return []
    chunk_ms = (BINANCE_LIMIT - 1) * PERIOD_MS  # ~41.5h per chunk
    out: list[dict] = []
    seen: set[int] = set()
    cur = start_ms
    iters = 0
    while cur < end_ms:
        iters += 1
        if iters > 2000:  # safety net
            logger.warning("pagination iteration limit reached at cur=%s", cur)
            break
        win_end = min(cur + chunk_ms, end_ms)
        rows = _fetch_oi_5m_window(client, symbol, cur, win_end, base_url=base_url)
        last_ts = cur
        for r in rows:
            try:
                ts = int(r["timestamp"])
            except Exception:  # noqa: BLE001
                continue
            if ts in seen:
                continue
            seen.add(ts)
            out.append(r)
            if ts > last_ts:
                last_ts = ts
        # Advance: prefer last_ts+1, else the chunk window.
        cur = max(cur + chunk_ms + 1, last_ts + 1) if rows else cur + chunk_ms + 1
        time.sleep(0.15)  # gentle rate-limit
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def refresh_oi_parquet(
    *,
    symbol: str,
    blob_container_name: Optional[str] = None,
    blob_name: Optional[str] = None,
    local_path: Optional[Path] = None,
    binance_base_url: str = BINANCE_FAPI_DEFAULT,
    safety_margin_ms: int = 6 * 60 * 60 * 1000,  # re-fetch last 6h to repair gaps
) -> dict:
    """Incrementally refresh the OI 5m parquet for ``symbol``.

    Provide either (blob_container_name + blob_name) for blob storage, or
    ``local_path`` for filesystem-only updates. Returns a small status dict.
    """
    import pandas as pd

    sym = symbol.upper()
    use_blob = bool(blob_container_name and blob_name)
    if not use_blob and local_path is None:
        raise ValueError("Provide blob_container/blob_name or local_path.")

    # 1) Load existing parquet
    if use_blob:
        container = _blob_container_client(blob_container_name)
        existing, existed = _download_existing_parquet(container, blob_name)
    else:
        if local_path.exists():  # type: ignore[union-attr]
            existing = pd.read_parquet(local_path).sort_values("timestamp").reset_index(drop=True)
            existed = True
        else:
            existing = pd.DataFrame(columns=["timestamp", "sum_oi", "sum_oi_value"])
            existed = False

    last_ts = int(existing["timestamp"].max()) if len(existing) else 0
    now_ms = int(time.time() * 1000)

    # 2) Decide fetch window
    if last_ts == 0:
        # No existing parquet: only fetch the maximum allowed window (~30d).
        start_ms = now_ms - BINANCE_MAX_LOOKBACK_MS + 5 * 60 * 1000
        logger.warning(
            "no existing parquet; seeding with last 30d only (use ingest_perp_meta.py for full history)"
        )
    else:
        # Repair the tail in case some prior fetch missed late-arriving 5m rows.
        start_ms = last_ts + 1 - safety_margin_ms
        # Clip to Binance's 30-day window.
        floor_ms = now_ms - BINANCE_MAX_LOOKBACK_MS + 5 * 60 * 1000
        if start_ms < floor_ms:
            logger.warning(
                "parquet last_ts is older than Binance 30d window; "
                "data gap will form. last_ts=%s now=%s", last_ts, now_ms
            )
            start_ms = floor_ms

    # End at start of current 5m bucket so we never write a partial value.
    end_ms = (now_ms // PERIOD_MS) * PERIOD_MS

    if start_ms >= end_ms:
        return {
            "symbol": sym,
            "existed": existed,
            "fetched": 0,
            "appended": 0,
            "rows_total": int(len(existing)),
            "last_ts_before": last_ts,
            "last_ts_after": last_ts,
            "uploaded_bytes": 0,
        }

    # 3) Fetch new rows
    logger.info(
        "[%s] fetching OI from %s..%s (existing rows=%d, last_ts=%s)",
        sym, start_ms, end_ms, len(existing), last_ts,
    )
    with httpx.Client() as http:
        new_rows = _fetch_oi_5m_range(
            http, sym, start_ms, end_ms, base_url=binance_base_url
        )

    if not new_rows:
        logger.info("[%s] no new rows from binance", sym)
        return {
            "symbol": sym,
            "existed": existed,
            "fetched": 0,
            "appended": 0,
            "rows_total": int(len(existing)),
            "last_ts_before": last_ts,
            "last_ts_after": last_ts,
            "uploaded_bytes": 0,
        }

    new_df = pd.DataFrame(new_rows)
    new_df["timestamp"] = new_df["timestamp"].astype("int64")
    new_df["sum_oi"] = new_df["sumOpenInterest"].astype(float)
    new_df["sum_oi_value"] = new_df["sumOpenInterestValue"].astype(float)
    new_df = new_df[["timestamp", "sum_oi", "sum_oi_value"]]

    # 4) Merge / dedupe
    before = len(existing)
    merged = (
        pd.concat([existing, new_df], ignore_index=True)
        .drop_duplicates("timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    appended = len(merged) - before

    # 5) Persist
    if use_blob:
        uploaded = _upload_parquet(container, blob_name, merged)
    else:
        local_path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        merged.to_parquet(local_path, index=False)
        uploaded = local_path.stat().st_size  # type: ignore[union-attr]

    last_ts_after = int(merged["timestamp"].max()) if len(merged) else last_ts
    logger.info(
        "[%s] refresh done: existed=%s fetched=%d appended=%d total=%d "
        "last_ts %s -> %s, uploaded=%d bytes",
        sym, existed, len(new_rows), appended, len(merged),
        last_ts, last_ts_after, uploaded,
    )
    return {
        "symbol": sym,
        "existed": existed,
        "fetched": len(new_rows),
        "appended": appended,
        "rows_total": int(len(merged)),
        "last_ts_before": last_ts,
        "last_ts_after": last_ts_after,
        "uploaded_bytes": uploaded,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--blob-container", default=os.environ.get("OI_PARQUET_BLOB_CONTAINER", ""))
    parser.add_argument("--blob-name", default="")
    parser.add_argument("--local-path", default="")
    parser.add_argument("--binance-base", default=os.environ.get("BINANCE_FAPI", BINANCE_FAPI_DEFAULT))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("azure").setLevel(logging.WARNING)

    sym = args.symbol.upper()
    blob_name = args.blob_name or os.environ.get(f"OI_PARQUET_BLOB_NAME_{sym}", "")
    local_path = Path(args.local_path) if args.local_path else None
    if not (args.blob_container and blob_name) and local_path is None:
        # default to repo-local path
        local_path = (
            Path(__file__).resolve().parents[1]
            / "data" / "perp_meta" / f"{sym}_oi_5m.parquet"
        )
        logger.info("no blob configured; refreshing local %s", local_path)

    result = refresh_oi_parquet(
        symbol=sym,
        blob_container_name=args.blob_container or None,
        blob_name=blob_name or None,
        local_path=local_path,
        binance_base_url=args.binance_base,
    )
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
