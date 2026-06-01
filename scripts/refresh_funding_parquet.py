"""Incrementally refresh per-symbol funding-rate history as Parquet in Blob.

Mirrors the design of ``scripts/refresh_oi_parquet.py`` but for the Binance
USDM ``fundingRate`` endpoint, which (unlike ``openInterestHist``) serves the
**entire contract life** — so no 30-day gap can form.

Data lake layout (Hive-style partitioning, container ``market-data``)::

    funding-rates/version=1/symbol=BTCUSDT/data.parquet
    funding-rates/version=1/symbol=SOLUSDT/data.parquet

Parquet schema: ``funding_time`` (int64 ms), ``funding_rate`` (float),
``mark_price`` (float).

Auth (mirrors ``refresh_oi_parquet`` / ``oi_provider``):
- ``AZURE_BLOB_CONNECTION_STRING`` (preferred), or
- ``AZURE_BLOB_ACCOUNT_URL`` + managed identity / Azure CLI fallback.

Usage (CLI)::

    # Single symbol -> blob
    python scripts/refresh_funding_parquet.py --symbol BTCUSDT \
        --blob-container market-data

    # Top-50 USDT-PERP by 24h quote volume -> blob (cron entry point)
    python scripts/refresh_funding_parquet.py --all-symbols --top-n 50 \
        --blob-container market-data

    # Local dry-run (no Azure needed)
    python scripts/refresh_funding_parquet.py --symbol BTCUSDT \
        --local-dir data/funding_rates
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

import httpx

logger = logging.getLogger("refresh_funding_parquet")

BINANCE_FAPI_DEFAULT = "https://fapi.binance.com"
FUNDING_PATH = "/fapi/v1/fundingRate"
TICKER_24H_PATH = "/fapi/v1/ticker/24hr"
EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
BINANCE_LIMIT = 1000  # fundingRate hard cap per request
DEFAULT_BLOB_CONTAINER = "market-data"
DEFAULT_BLOB_PREFIX = "funding-rates/version=1"
PARQUET_COLUMNS = ["funding_time", "funding_rate", "mark_price"]


# ---------------------------------------------------------------------------
# Blob helpers (auth chain mirrors refresh_oi_parquet._blob_container_client)
# ---------------------------------------------------------------------------
def _blob_container_client(container_name: str):
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


def _blob_name(prefix: str, symbol: str) -> str:
    return f"{prefix.rstrip('/')}/symbol={symbol.upper()}/data.parquet"


def _download_existing_parquet(container, blob_name: str):
    """Return (DataFrame, exists). Missing blob -> empty df."""
    import pandas as pd

    blob = container.get_blob_client(blob_name)
    try:
        data = blob.download_blob().readall()
    except Exception as exc:  # noqa: BLE001
        logger.info("blob %s not found (%s); will create new one", blob_name, exc)
        return pd.DataFrame(columns=PARQUET_COLUMNS), False
    df = pd.read_parquet(io.BytesIO(data))
    return df.sort_values("funding_time").reset_index(drop=True), True


def _upload_parquet(container, blob_name: str, df) -> int:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    payload = buf.getvalue()
    container.get_blob_client(blob_name).upload_blob(payload, overwrite=True)
    return len(payload)


# ---------------------------------------------------------------------------
# Universe selection
# ---------------------------------------------------------------------------
def select_universe(
    client: httpx.Client, *, top_n: int, base_url: str
) -> list[str]:
    """Top ``top_n`` USDT perpetual symbols by 24h quote volume.

    Filters to ``contractType=PERPETUAL``, ``quoteAsset=USDT``,
    ``status=TRADING`` via exchangeInfo, then ranks by ``ticker/24hr``
    ``quoteVolume``.
    """
    info = client.get(f"{base_url}{EXCHANGE_INFO_PATH}", timeout=30.0).json()
    eligible: set[str] = set()
    for s in info.get("symbols", []):
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ):
            eligible.add(s["symbol"])

    tickers = client.get(f"{base_url}{TICKER_24H_PATH}", timeout=30.0).json()
    ranked: list[tuple[str, float]] = []
    for t in tickers:
        sym = t.get("symbol")
        if sym in eligible:
            try:
                ranked.append((sym, float(t.get("quoteVolume", 0.0))))
            except (TypeError, ValueError):
                continue
    ranked.sort(key=lambda x: x[1], reverse=True)
    universe = [sym for sym, _ in ranked[:top_n]]
    logger.info(
        "universe: %d eligible USDT-PERP, selected top %d by quoteVolume",
        len(eligible), len(universe),
    )
    return universe


# ---------------------------------------------------------------------------
# Binance funding fetch (paginated; advances by last fundingTime)
# ---------------------------------------------------------------------------
def _fetch_funding_window(
    client: httpx.Client, symbol: str, start_ms: int, end_ms: int, *, base_url: str
) -> list[dict]:
    params = {
        "symbol": symbol,
        "startTime": int(start_ms),
        "endTime": int(end_ms),
        "limit": BINANCE_LIMIT,
    }
    for attempt in range(5):
        try:
            resp = client.get(f"{base_url}{FUNDING_PATH}", params=params, timeout=30.0)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt
                logger.warning("funding fetch http=%s, sleep %ds", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() or []
        except httpx.HTTPError as exc:
            if attempt == 4:
                raise
            logger.warning("funding fetch error attempt=%d: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return []


def fetch_funding_range(
    client: httpx.Client, symbol: str, start_ms: int, end_ms: int, *, base_url: str
):
    """Paginate ``fundingRate`` across [start_ms, end_ms]. Returns a DataFrame."""
    import pandas as pd

    if start_ms >= end_ms:
        return pd.DataFrame(columns=PARQUET_COLUMNS)

    out: list[dict] = []
    seen: set[int] = set()
    cur = start_ms
    iters = 0
    while cur < end_ms:
        iters += 1
        if iters > 5000:  # safety net
            logger.warning("pagination limit reached for %s at cur=%s", symbol, cur)
            break
        rows = _fetch_funding_window(client, symbol, cur, end_ms, base_url=base_url)
        if not rows:
            break
        last_ts = cur
        for r in rows:
            try:
                ts = int(r["fundingTime"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts in seen:
                continue
            seen.add(ts)
            out.append(r)
            if ts > last_ts:
                last_ts = ts
        if last_ts <= cur:
            break
        cur = last_ts + 1
        time.sleep(0.15)  # gentle rate-limit

    if not out:
        return pd.DataFrame(columns=PARQUET_COLUMNS)
    df = pd.DataFrame(out)
    df["funding_time"] = df["fundingTime"].astype("int64")
    df["funding_rate"] = df["fundingRate"].astype(float)
    if "markPrice" in df.columns:
        df["mark_price"] = pd.to_numeric(df["markPrice"], errors="coerce")
    else:
        df["mark_price"] = float("nan")
    df = (
        df[PARQUET_COLUMNS]
        .drop_duplicates("funding_time", keep="last")
        .sort_values("funding_time")
        .reset_index(drop=True)
    )
    return df


# ---------------------------------------------------------------------------
# Per-symbol refresh
# ---------------------------------------------------------------------------
def refresh_symbol(
    *,
    symbol: str,
    container=None,
    blob_prefix: str = DEFAULT_BLOB_PREFIX,
    local_dir: Optional[Path] = None,
    binance_base_url: str = BINANCE_FAPI_DEFAULT,
    http: Optional[httpx.Client] = None,
    safety_margin_ms: int = 24 * 60 * 60 * 1000,  # re-fetch last 24h to repair tail
) -> dict:
    """Incrementally refresh funding parquet for one ``symbol``.

    Provide either ``container`` (+ ``blob_prefix``) for blob storage, or
    ``local_dir`` for filesystem-only updates.
    """
    import pandas as pd

    sym = symbol.upper()
    use_blob = container is not None
    if not use_blob and local_dir is None:
        raise ValueError("Provide container or local_dir.")

    blob_name = _blob_name(blob_prefix, sym)
    local_path = (local_dir / f"symbol={sym}" / "data.parquet") if local_dir else None

    # 1) Load existing
    if use_blob:
        existing, existed = _download_existing_parquet(container, blob_name)
    elif local_path.exists():  # type: ignore[union-attr]
        existing = pd.read_parquet(local_path).sort_values("funding_time").reset_index(drop=True)
        existed = True
    else:
        existing = pd.DataFrame(columns=PARQUET_COLUMNS)
        existed = False

    last_ts = int(existing["funding_time"].max()) if len(existing) else 0
    now_ms = int(time.time() * 1000)

    # 2) Decide fetch window
    if last_ts == 0:
        start_ms = 0  # full contract history (fundingRate has no 30d cap)
        logger.info("[%s] seeding full funding history", sym)
    else:
        start_ms = max(0, last_ts + 1 - safety_margin_ms)

    owns_http = http is None
    client = http or httpx.Client()
    try:
        new_df = fetch_funding_range(client, sym, start_ms, now_ms, base_url=binance_base_url)
    finally:
        if owns_http:
            client.close()

    if new_df.empty:
        logger.info("[%s] no new funding rows", sym)
        return {
            "symbol": sym, "existed": existed, "fetched": 0, "appended": 0,
            "rows_total": int(len(existing)), "last_ts_before": last_ts,
            "last_ts_after": last_ts, "uploaded_bytes": 0,
        }

    # 3) Merge / dedupe
    before = len(existing)
    merged = (
        pd.concat([existing, new_df], ignore_index=True)
        .drop_duplicates("funding_time", keep="last")
        .sort_values("funding_time")
        .reset_index(drop=True)
    )
    appended = len(merged) - before

    # 4) Persist
    if use_blob:
        uploaded = _upload_parquet(container, blob_name, merged)
    else:
        local_path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        merged.to_parquet(local_path, index=False)
        uploaded = local_path.stat().st_size  # type: ignore[union-attr]

    last_ts_after = int(merged["funding_time"].max())
    logger.info(
        "[%s] refresh done: existed=%s fetched=%d appended=%d total=%d "
        "last_ts %s -> %s, uploaded=%d bytes",
        sym, existed, len(new_df), appended, len(merged),
        last_ts, last_ts_after, uploaded,
    )
    return {
        "symbol": sym, "existed": existed, "fetched": int(len(new_df)),
        "appended": int(appended), "rows_total": int(len(merged)),
        "last_ts_before": last_ts, "last_ts_after": last_ts_after,
        "uploaded_bytes": uploaded,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", help="single symbol, e.g. BTCUSDT")
    group.add_argument("--all-symbols", action="store_true",
                       help="refresh top-N USDT-PERP universe by 24h quote volume")
    parser.add_argument("--top-n", type=int,
                        default=int(os.environ.get("FUNDING_TOP_N", "50")))
    parser.add_argument("--blob-container",
                        default=os.environ.get("FUNDING_BLOB_CONTAINER", DEFAULT_BLOB_CONTAINER))
    parser.add_argument("--blob-prefix",
                        default=os.environ.get("FUNDING_BLOB_PREFIX", DEFAULT_BLOB_PREFIX))
    parser.add_argument("--local-dir", default="",
                        help="write to local filesystem instead of blob (dry-run)")
    parser.add_argument("--binance-base",
                        default=os.environ.get("BINANCE_FAPI", BINANCE_FAPI_DEFAULT))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("azure").setLevel(logging.WARNING)

    use_local = bool(args.local_dir)
    local_dir = Path(args.local_dir) if use_local else None
    container = None if use_local else _blob_container_client(args.blob_container)

    with httpx.Client() as http:
        if args.all_symbols:
            symbols = select_universe(http, top_n=args.top_n, base_url=args.binance_base)
        else:
            symbols = [args.symbol.upper()]

        results = []
        failures = 0
        for sym in symbols:
            try:
                res = refresh_symbol(
                    symbol=sym,
                    container=container,
                    blob_prefix=args.blob_prefix,
                    local_dir=local_dir,
                    binance_base_url=args.binance_base,
                    http=http,
                )
                results.append(res)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.error("refresh failed for %s: %s", sym, exc)

    total_appended = sum(r["appended"] for r in results)
    logger.info(
        "done: symbols=%d ok=%d failed=%d total_appended=%d",
        len(symbols), len(results), failures, total_appended,
    )
    return 1 if failures and not results else 0


if __name__ == "__main__":
    sys.exit(_cli())
