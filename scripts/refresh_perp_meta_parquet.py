"""Incremental refresh of perp-meta + klines parquets to Azure Blob.

Mirrors ``scripts/refresh_oi_parquet.py``'s shape but covers four parquet
families used by ``MultiFactorPortfolioStrategy``:

  - funding   <SYMBOL>_funding.parquet      ← /fapi/v1/fundingRate
  - taker     <SYMBOL>_taker_5m.parquet     ← /futures/data/takerlongshortRatio
  - lsr       <SYMBOL>_lsr_5m.parquet       ← /futures/data/globalLongShortAccountRatio
  - klines    <SYMBOL>_15m_klines.parquet   ← /fapi/v1/klines (interval=15m)

Each function downloads the existing parquet from blob (or local file),
fetches new rows from Binance USDM Futures since ``last_ts``, merges with
dedup on the timestamp column, sorts, and uploads back. Designed to be
called periodically from inside ``perp_meta_ingestor.py`` (default cadence
6h, mirroring oi_ingestor's parquet refresh loop).

Auth (mirrors ``refresh_oi_parquet._blob_container_client``):
- ``AZURE_BLOB_CONNECTION_STRING`` (preferred for local), or
- ``AZURE_BLOB_ACCOUNT_URL`` + managed identity / Azure CLI fallback.

Binance constraints:
- ``/futures/data/*`` endpoints (taker, lsr) only serve the **last 30 days**.
  A blob parquet older than that will form an unrecoverable gap; the
  caller is expected to refresh at least every ~25 days.
- ``/fapi/v1/fundingRate`` and ``/fapi/v1/klines`` cover full history, so
  funding and klines can recover from arbitrarily large gaps.

Usage (CLI)::

    python scripts/refresh_perp_meta_parquet.py \
        --symbol BTCUSDT --kinds funding,taker,lsr,klines \
        --blob-container market-data --blob-prefix perp_meta
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

logger = logging.getLogger("refresh_perp_meta_parquet")

BINANCE_FAPI_DEFAULT = "https://fapi.binance.com"
BINANCE_FUTURES_DATA_LIMIT = 500  # /futures/data/* hard cap
BINANCE_FUTURES_DATA_LOOKBACK_MS = 30 * 24 * 3600 * 1000
BINANCE_KLINES_LIMIT = 1500
KLINES_15M_MS = 15 * 60 * 1000
PERIOD_5M_MS = 5 * 60 * 1000

ALL_KINDS = ("funding", "taker", "lsr", "klines")


# ---------------------------------------------------------------------------
# Blob helpers (verbatim port from refresh_oi_parquet.py)
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


def _download_existing(container, blob_name: str, *, columns: list[str]):
    """Return (DataFrame, exists). When blob is missing, returns empty df."""
    import pandas as pd

    blob = container.get_blob_client(blob_name)
    try:
        data = blob.download_blob().readall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("blob %s not found (%s); will create new one", blob_name, exc)
        return pd.DataFrame(columns=columns), False
    df = pd.read_parquet(io.BytesIO(data))
    return df, True


def _upload(container, blob_name: str, df) -> int:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    payload = buf.getvalue()
    container.get_blob_client(blob_name).upload_blob(payload, overwrite=True)
    return len(payload)


def _load_existing(
    *,
    use_blob: bool,
    container,
    blob_name: Optional[str],
    local_path: Optional[Path],
    columns: list[str],
):
    import pandas as pd

    if use_blob:
        return _download_existing(container, blob_name, columns=columns)
    if local_path and local_path.exists():
        return pd.read_parquet(local_path), True
    return pd.DataFrame(columns=columns), False


def _persist(
    merged,
    *,
    use_blob: bool,
    container,
    blob_name: Optional[str],
    local_path: Optional[Path],
) -> int:
    if use_blob:
        return _upload(container, blob_name, merged)
    assert local_path is not None
    local_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(local_path, index=False)
    return local_path.stat().st_size


# ---------------------------------------------------------------------------
# Binance fetchers — funding / taker / lsr / klines
# ---------------------------------------------------------------------------
def _http_get_json(client: httpx.Client, url: str, params: dict) -> list[dict]:
    for attempt in range(5):
        try:
            resp = client.get(url, params=params, timeout=20.0)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt
                logger.warning("http=%s, sleep %ds (%s)", resp.status_code, wait, url)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() or []
        except httpx.HTTPError as exc:
            if attempt == 4:
                raise
            logger.warning("http error attempt=%d: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return []


def _fetch_funding_range(
    client: httpx.Client,
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    base_url: str,
) -> list[dict]:
    """Paginate /fapi/v1/fundingRate over [start_ms, end_ms]."""
    if start_ms >= end_ms:
        return []
    out: list[dict] = []
    seen: set[int] = set()
    cur = start_ms
    iters = 0
    while cur <= end_ms:
        iters += 1
        if iters > 1000:
            logger.warning("funding pagination iter limit at cur=%d", cur)
            break
        rows = _http_get_json(
            client,
            f"{base_url}/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": cur, "endTime": end_ms, "limit": 1000},
        )
        if not rows:
            break
        last_ts = cur
        for r in rows:
            try:
                ts = int(r["fundingTime"])
            except Exception:  # noqa: BLE001
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
        if len(rows) < 1000:
            break
        time.sleep(0.15)
    return out


def _fetch_futures_data_5m(
    client: httpx.Client,
    *,
    path: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    base_url: str,
) -> list[dict]:
    """Paginate /futures/data/{path} (taker / lsr) at 5m period."""
    if start_ms >= end_ms:
        return []
    chunk_ms = (BINANCE_FUTURES_DATA_LIMIT - 1) * PERIOD_5M_MS
    out: list[dict] = []
    seen: set[int] = set()
    cur = start_ms
    iters = 0
    while cur < end_ms:
        iters += 1
        if iters > 2000:
            logger.warning("%s pagination iter limit at cur=%d", path, cur)
            break
        win_end = min(cur + chunk_ms, end_ms)
        rows = _http_get_json(
            client,
            f"{base_url}/futures/data/{path}",
            {
                "symbol": symbol,
                "period": "5m",
                "limit": BINANCE_FUTURES_DATA_LIMIT,
                "startTime": cur,
                "endTime": win_end,
            },
        )
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
        cur = max(cur + chunk_ms + 1, last_ts + 1) if rows else cur + chunk_ms + 1
        time.sleep(0.15)
    return out


def _fetch_klines_15m(
    client: httpx.Client,
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    base_url: str,
) -> list[list]:
    """Paginate /fapi/v1/klines at 15m interval."""
    if start_ms >= end_ms:
        return []
    out: list[list] = []
    cur = start_ms
    iters = 0
    while cur <= end_ms:
        iters += 1
        if iters > 5000:
            logger.warning("klines pagination iter limit at cur=%d", cur)
            break
        params = {
            "symbol": symbol,
            "interval": "15m",
            "startTime": cur,
            "endTime": end_ms,
            "limit": BINANCE_KLINES_LIMIT,
        }
        rows = _http_get_json(client, f"{base_url}/fapi/v1/klines", params)
        if not rows:
            break
        out.extend(rows)
        last_open = int(rows[-1][0])
        if last_open <= cur:
            break
        cur = last_open + KLINES_15M_MS
        if len(rows) < BINANCE_KLINES_LIMIT:
            break
        time.sleep(0.05)
    return out


# ---------------------------------------------------------------------------
# Per-kind public refresh entry points
# ---------------------------------------------------------------------------
def _now_floor_5m_ms() -> int:
    return (int(time.time() * 1000) // PERIOD_5M_MS) * PERIOD_5M_MS


def _now_floor_15m_ms() -> int:
    return (int(time.time() * 1000) // KLINES_15M_MS) * KLINES_15M_MS


def _refresh_funding(
    *,
    symbol: str,
    use_blob: bool,
    container,
    blob_name: Optional[str],
    local_path: Optional[Path],
    base_url: str,
    safety_margin_ms: int,
) -> dict:
    import pandas as pd

    cols = ["funding_time", "funding_rate"]
    existing, existed = _load_existing(
        use_blob=use_blob,
        container=container,
        blob_name=blob_name,
        local_path=local_path,
        columns=cols,
    )
    last_ts = int(existing["funding_time"].max()) if len(existing) else 0
    now_ms = int(time.time() * 1000)
    start_ms = (last_ts + 1 - safety_margin_ms) if last_ts else (now_ms - 60 * 24 * 3600 * 1000)
    if start_ms < 0:
        start_ms = 0
    if start_ms >= now_ms:
        return _empty_result(symbol, "funding", existed, last_ts, len(existing))

    with httpx.Client() as http:
        rows = _fetch_funding_range(http, symbol, start_ms, now_ms, base_url=base_url)
    if not rows:
        return _empty_result(symbol, "funding", existed, last_ts, len(existing))

    new_df = pd.DataFrame(rows)
    new_df["funding_time"] = new_df["fundingTime"].astype("int64")
    new_df["funding_rate"] = new_df["fundingRate"].astype(float)
    new_df = new_df[cols]
    return _merge_persist(
        existing=existing,
        new_df=new_df,
        key="funding_time",
        symbol=symbol,
        kind="funding",
        existed=existed,
        last_ts_before=last_ts,
        use_blob=use_blob,
        container=container,
        blob_name=blob_name,
        local_path=local_path,
    )


def _refresh_taker(
    *,
    symbol: str,
    use_blob: bool,
    container,
    blob_name: Optional[str],
    local_path: Optional[Path],
    base_url: str,
    safety_margin_ms: int,
) -> dict:
    import pandas as pd

    cols = ["timestamp", "sum_taker_long_short_vol_ratio"]
    existing, existed = _load_existing(
        use_blob=use_blob, container=container, blob_name=blob_name,
        local_path=local_path, columns=cols,
    )
    last_ts = int(existing["timestamp"].max()) if len(existing) else 0
    now_ms = _now_floor_5m_ms()
    start_ms = (last_ts + 1 - safety_margin_ms) if last_ts else (
        now_ms - BINANCE_FUTURES_DATA_LOOKBACK_MS + PERIOD_5M_MS
    )
    floor_ms = now_ms - BINANCE_FUTURES_DATA_LOOKBACK_MS + PERIOD_5M_MS
    if last_ts and start_ms < floor_ms:
        logger.warning(
            "[taker] parquet last_ts=%d older than 30d window; gap will form", last_ts,
        )
        start_ms = floor_ms
    if start_ms >= now_ms:
        return _empty_result(symbol, "taker", existed, last_ts, len(existing))

    with httpx.Client() as http:
        rows = _fetch_futures_data_5m(
            http, path="takerlongshortRatio", symbol=symbol,
            start_ms=start_ms, end_ms=now_ms, base_url=base_url,
        )
    if not rows:
        return _empty_result(symbol, "taker", existed, last_ts, len(existing))

    new_df = pd.DataFrame(rows)
    new_df["timestamp"] = new_df["timestamp"].astype("int64")
    new_df["sum_taker_long_short_vol_ratio"] = new_df["buySellRatio"].astype(float)
    new_df = new_df[cols]
    return _merge_persist(
        existing=existing,
        new_df=new_df,
        key="timestamp",
        symbol=symbol,
        kind="taker",
        existed=existed,
        last_ts_before=last_ts,
        use_blob=use_blob,
        container=container,
        blob_name=blob_name,
        local_path=local_path,
    )


def _refresh_lsr(
    *,
    symbol: str,
    use_blob: bool,
    container,
    blob_name: Optional[str],
    local_path: Optional[Path],
    base_url: str,
    safety_margin_ms: int,
) -> dict:
    import pandas as pd

    # Match the schema produced by backfill_vision.py so the multi-factor
    # strategy's _load_unified_dataset (which reads `count_long_short_ratio`)
    # keeps working. We populate count_long_short_ratio from
    # /futures/data/globalLongShortAccountRatio's `longShortRatio` field.
    cols = [
        "timestamp",
        "count_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_toptrader_long_short_ratio",
    ]
    existing, existed = _load_existing(
        use_blob=use_blob, container=container, blob_name=blob_name,
        local_path=local_path, columns=cols,
    )
    last_ts = int(existing["timestamp"].max()) if len(existing) else 0
    now_ms = _now_floor_5m_ms()
    start_ms = (last_ts + 1 - safety_margin_ms) if last_ts else (
        now_ms - BINANCE_FUTURES_DATA_LOOKBACK_MS + PERIOD_5M_MS
    )
    floor_ms = now_ms - BINANCE_FUTURES_DATA_LOOKBACK_MS + PERIOD_5M_MS
    if last_ts and start_ms < floor_ms:
        logger.warning(
            "[lsr] parquet last_ts=%d older than 30d window; gap will form", last_ts,
        )
        start_ms = floor_ms
    if start_ms >= now_ms:
        return _empty_result(symbol, "lsr", existed, last_ts, len(existing))

    with httpx.Client() as http:
        global_rows = _fetch_futures_data_5m(
            http, path="globalLongShortAccountRatio", symbol=symbol,
            start_ms=start_ms, end_ms=now_ms, base_url=base_url,
        )
        top_rows = _fetch_futures_data_5m(
            http, path="topLongShortAccountRatio", symbol=symbol,
            start_ms=start_ms, end_ms=now_ms, base_url=base_url,
        )

    if not global_rows and not top_rows:
        return _empty_result(symbol, "lsr", existed, last_ts, len(existing))

    glob_df = pd.DataFrame(global_rows)
    if len(glob_df):
        glob_df["timestamp"] = glob_df["timestamp"].astype("int64")
        glob_df["count_long_short_ratio"] = glob_df["longShortRatio"].astype(float)
        glob_df = glob_df[["timestamp", "count_long_short_ratio"]]
    else:
        glob_df = pd.DataFrame(columns=["timestamp", "count_long_short_ratio"])

    top_df = pd.DataFrame(top_rows)
    if len(top_df):
        top_df["timestamp"] = top_df["timestamp"].astype("int64")
        top_df["sum_toptrader_long_short_ratio"] = top_df["longShortRatio"].astype(float)
        # Top trader 'count' variant is approximated by the same ratio (the
        # daily Vision archive distinguishes count vs sum, but the fapi
        # endpoint exposes only one composite ratio). We mirror the value
        # so downstream consumers keep a non-null column; only the columns
        # actually read by the strategy (count_long_short_ratio) need to
        # be precise.
        top_df["count_toptrader_long_short_ratio"] = top_df["sum_toptrader_long_short_ratio"]
        top_df = top_df[
            [
                "timestamp",
                "sum_toptrader_long_short_ratio",
                "count_toptrader_long_short_ratio",
            ]
        ]
    else:
        top_df = pd.DataFrame(
            columns=[
                "timestamp",
                "sum_toptrader_long_short_ratio",
                "count_toptrader_long_short_ratio",
            ]
        )

    new_df = glob_df.merge(top_df, on="timestamp", how="outer").sort_values("timestamp")
    # Fill any missing columns that the merge skipped because of empty side.
    for c in cols:
        if c not in new_df.columns:
            new_df[c] = float("nan")
    new_df = new_df[cols]

    return _merge_persist(
        existing=existing,
        new_df=new_df,
        key="timestamp",
        symbol=symbol,
        kind="lsr",
        existed=existed,
        last_ts_before=last_ts,
        use_blob=use_blob,
        container=container,
        blob_name=blob_name,
        local_path=local_path,
    )


def _refresh_klines(
    *,
    symbol: str,
    use_blob: bool,
    container,
    blob_name: Optional[str],
    local_path: Optional[Path],
    base_url: str,
    safety_margin_ms: int,
) -> dict:
    import pandas as pd

    cols = ["ts", "o", "h", "l", "c"]
    existing, existed = _load_existing(
        use_blob=use_blob, container=container, blob_name=blob_name,
        local_path=local_path, columns=cols,
    )
    last_ts = int(existing["ts"].max()) if len(existing) else 0
    end_ms = _now_floor_15m_ms() - 1  # last fully-closed 15m boundary - 1ms
    if last_ts:
        start_ms = max(0, last_ts + 1 - safety_margin_ms)
    else:
        # No seed: fallback to last 60 days (parquet seed gets primed by
        # backfill_vision.py / refresh_klines_parquet.py one-shot).
        start_ms = end_ms - 60 * 24 * 3600 * 1000
    if start_ms >= end_ms:
        return _empty_result(symbol, "klines", existed, last_ts, len(existing))

    with httpx.Client() as http:
        rows = _fetch_klines_15m(http, symbol, start_ms, end_ms, base_url=base_url)
    if not rows:
        return _empty_result(symbol, "klines", existed, last_ts, len(existing))

    new_df = pd.DataFrame(rows, columns=[
        "ts", "o", "h", "l", "c", "v", "ct", "qv", "nt", "tb", "tq", "i",
    ])[cols]
    new_df["ts"] = new_df["ts"].astype("int64")
    for col in ("o", "h", "l", "c"):
        new_df[col] = new_df[col].astype("float64")
    return _merge_persist(
        existing=existing,
        new_df=new_df,
        key="ts",
        symbol=symbol,
        kind="klines",
        existed=existed,
        last_ts_before=last_ts,
        use_blob=use_blob,
        container=container,
        blob_name=blob_name,
        local_path=local_path,
    )


# ---------------------------------------------------------------------------
# Shared merge / persist / result helpers
# ---------------------------------------------------------------------------
def _merge_persist(
    *,
    existing,
    new_df,
    key: str,
    symbol: str,
    kind: str,
    existed: bool,
    last_ts_before: int,
    use_blob: bool,
    container,
    blob_name: Optional[str],
    local_path: Optional[Path],
) -> dict:
    import pandas as pd

    before = len(existing)
    merged = (
        pd.concat([existing, new_df], ignore_index=True)
        .drop_duplicates(key, keep="last")
        .sort_values(key)
        .reset_index(drop=True)
    )
    appended = len(merged) - before
    uploaded = _persist(
        merged,
        use_blob=use_blob,
        container=container,
        blob_name=blob_name,
        local_path=local_path,
    )
    last_ts_after = int(merged[key].max()) if len(merged) else last_ts_before
    logger.info(
        "[%s/%s] existed=%s fetched=%d appended=%d total=%d last_ts %s -> %s, uploaded=%d bytes",
        symbol, kind, existed, len(new_df), appended, len(merged),
        last_ts_before, last_ts_after, uploaded,
    )
    return {
        "symbol": symbol,
        "kind": kind,
        "existed": existed,
        "fetched": int(len(new_df)),
        "appended": int(appended),
        "rows_total": int(len(merged)),
        "last_ts_before": int(last_ts_before),
        "last_ts_after": int(last_ts_after),
        "uploaded_bytes": int(uploaded),
    }


def _empty_result(symbol: str, kind: str, existed: bool,
                  last_ts: int, rows_total: int) -> dict:
    return {
        "symbol": symbol,
        "kind": kind,
        "existed": existed,
        "fetched": 0,
        "appended": 0,
        "rows_total": int(rows_total),
        "last_ts_before": int(last_ts),
        "last_ts_after": int(last_ts),
        "uploaded_bytes": 0,
    }


_KIND_REFRESH_FN = {
    "funding": _refresh_funding,
    "taker": _refresh_taker,
    "lsr": _refresh_lsr,
    "klines": _refresh_klines,
}

_KIND_BLOB_NAME_FMT = {
    "funding": "{symbol}_funding.parquet",
    "taker": "{symbol}_taker_5m.parquet",
    "lsr": "{symbol}_lsr_5m.parquet",
    "klines": "{symbol}_15m_klines.parquet",
}

# Per-kind safety-margin (re-fetch) windows. Funding cadence is 8h so we
# overlap a full cycle. taker/lsr/klines are 5m/15m so 6h is plenty.
_KIND_DEFAULT_SAFETY_MS = {
    "funding": 24 * 3600 * 1000,
    "taker": 6 * 3600 * 1000,
    "lsr": 6 * 3600 * 1000,
    "klines": 2 * 3600 * 1000,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def refresh_perp_meta_parquet(
    *,
    symbol: str,
    kinds: Iterable[str],
    blob_container_name: Optional[str] = None,
    blob_prefix: Optional[str] = None,
    blob_names: Optional[dict[str, str]] = None,
    local_dir: Optional[Path] = None,
    binance_base_url: str = BINANCE_FAPI_DEFAULT,
    safety_margin_ms: Optional[dict[str, int]] = None,
) -> dict[str, dict]:
    """Refresh one or more parquet kinds for ``symbol``.

    Provide either:
      - ``blob_container_name`` (+ optional ``blob_prefix`` or per-kind
        ``blob_names``) for blob storage, or
      - ``local_dir`` for filesystem-only updates.

    Returns ``{kind: status_dict}``. Any kind that errors is logged and
    returns ``{"error": str}`` so the caller can keep going for the others.
    """
    sym = symbol.upper()
    use_blob = bool(blob_container_name)
    if not use_blob and local_dir is None:
        raise ValueError("Provide blob_container_name or local_dir.")
    container = _blob_container_client(blob_container_name) if use_blob else None
    safety_margin_ms = safety_margin_ms or {}

    bad = [k for k in kinds if k not in _KIND_REFRESH_FN]
    if bad:
        raise ValueError(f"unknown kinds: {bad}; allowed={list(_KIND_REFRESH_FN)}")

    results: dict[str, dict] = {}
    for kind in kinds:
        fn = _KIND_REFRESH_FN[kind]
        if use_blob:
            blob_name = (blob_names or {}).get(kind)
            if not blob_name:
                fname = _KIND_BLOB_NAME_FMT[kind].format(symbol=sym)
                if blob_prefix:
                    blob_name = f"{blob_prefix.rstrip('/')}/{fname}"
                else:
                    blob_name = fname
            local_path = None
        else:
            blob_name = None
            local_path = local_dir / _KIND_BLOB_NAME_FMT[kind].format(symbol=sym)
        try:
            results[kind] = fn(
                symbol=sym,
                use_blob=use_blob,
                container=container,
                blob_name=blob_name,
                local_path=local_path,
                base_url=binance_base_url,
                safety_margin_ms=safety_margin_ms.get(
                    kind, _KIND_DEFAULT_SAFETY_MS[kind]
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s/%s] refresh failed: %s", sym, kind, exc, exc_info=True)
            results[kind] = {"symbol": sym, "kind": kind, "error": str(exc)}
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--kinds",
        default="funding,taker,lsr,klines",
        help=f"comma-separated subset of {ALL_KINDS}",
    )
    parser.add_argument(
        "--blob-container",
        default=os.environ.get("MFP_PARQUET_BLOB_CONTAINER", ""),
    )
    parser.add_argument(
        "--blob-prefix",
        default=os.environ.get("MFP_PARQUET_BLOB_PREFIX", ""),
    )
    parser.add_argument("--local-dir", default="")
    parser.add_argument(
        "--binance-base", default=os.environ.get("BINANCE_FAPI", BINANCE_FAPI_DEFAULT),
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("azure").setLevel(logging.WARNING)

    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    local_dir = Path(args.local_dir) if args.local_dir else None
    if not args.blob_container and local_dir is None:
        # default to repo-local data dir
        local_dir = Path(__file__).resolve().parents[1] / "data" / "perp_meta"
        logger.info("no blob configured; refreshing local %s", local_dir)

    results = refresh_perp_meta_parquet(
        symbol=args.symbol,
        kinds=kinds,
        blob_container_name=args.blob_container or None,
        blob_prefix=args.blob_prefix or None,
        local_dir=local_dir,
        binance_base_url=args.binance_base,
    )
    for k, r in results.items():
        print(f"  {k}: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
