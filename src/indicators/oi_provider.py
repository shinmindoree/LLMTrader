"""OI (Open Interest) data provider for the OI capitulation-bottom alpha.

Two backends:
- Backtest: load `data/perp_meta/{SYMBOL}_oi_5m.parquet` once, look up the latest
  OI value at-or-before each bar timestamp, and the OI value 24h earlier.
- Live: read from Redis sorted set `oi:{SYMBOL}:hist` populated by the
  `oi_ingestor` worker (each member is `"{ts_ms}:{sum_oi}"`, score = ts_ms).

Public API used by strategies:
    provider = get_oi_provider(symbol)
    pct = provider.pct_change(ref_ts_ms, lookback_ms=24*3600*1000)
    # returns float pct change of OI 24h ago vs current, or NaN when missing
"""
from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("llmtrader.oi_provider")

REDIS_OI_KEY_FMT = "oi:{symbol}:hist"
PARQUET_PATH_FMT = "data/perp_meta/{symbol}_oi_5m.parquet"
PARQUET_URL_ENV_FMT = "OI_PARQUET_URL_{symbol}"  # e.g. OI_PARQUET_URL_BTCUSDT
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOOKBACK_MS = 24 * 3600 * 1000


def _resolve_parquet_path(symbol: str) -> Path:
    """Locate the OI parquet for `symbol`, downloading it if a URL is configured.

    Resolution order:
      1. `OI_PARQUET_PATH_{SYMBOL}` env var (absolute path)
      2. `<repo_root>/data/perp_meta/{symbol}_oi_5m.parquet`
      3. `OI_PARQUET_URL_{SYMBOL}` env var → download to /tmp cache
    """
    sym = symbol.upper()
    explicit = os.environ.get(f"OI_PARQUET_PATH_{sym}", "").strip()
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        logger.warning("[oi] OI_PARQUET_PATH_%s set but file missing: %s", sym, p)

    local = PROJECT_ROOT / PARQUET_PATH_FMT.format(symbol=sym)
    if local.exists():
        return local

    url = os.environ.get(f"OI_PARQUET_URL_{sym}", "").strip()
    if url:
        cache_dir = Path(os.environ.get("OI_PARQUET_CACHE_DIR", "/tmp/oi_parquet"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{sym}_oi_5m.parquet"
        if not cache_path.exists():
            logger.info("[oi] downloading parquet for %s from %s", sym, url)
            import httpx
            with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
                resp.raise_for_status()
                with cache_path.open("wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1 << 16):
                        f.write(chunk)
            logger.info("[oi] downloaded %d bytes to %s", cache_path.stat().st_size, cache_path)
        return cache_path

    # Azure Blob fallback (managed identity / connection string).
    # Set OI_PARQUET_BLOB_CONTAINER + OI_PARQUET_BLOB_NAME_{SYMBOL}
    container_name = os.environ.get("OI_PARQUET_BLOB_CONTAINER", "").strip()
    blob_name = os.environ.get(f"OI_PARQUET_BLOB_NAME_{sym}", "").strip()
    if container_name and blob_name:
        cache_dir = Path(os.environ.get("OI_PARQUET_CACHE_DIR", "/tmp/oi_parquet"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{sym}_oi_5m.parquet"
        if not cache_path.exists():
            logger.info("[oi] downloading parquet for %s from blob %s/%s",
                        sym, container_name, blob_name)
            data = _download_blob(container_name, blob_name)
            cache_path.write_bytes(data)
            logger.info("[oi] downloaded %d bytes to %s", len(data), cache_path)
        return cache_path

    raise FileNotFoundError(
        f"OI parquet not found for {sym}. Provide one of: "
        f"OI_PARQUET_PATH_{sym}, file at {local}, OI_PARQUET_URL_{sym}, "
        f"or OI_PARQUET_BLOB_CONTAINER + OI_PARQUET_BLOB_NAME_{sym}."
    )


def _download_blob(container_name: str, blob_name: str) -> bytes:
    """Download a blob using the same auth chain as src/common/blob_storage.py."""
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    from azure.storage.blob import ContainerClient
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "").strip()
    if conn_str:
        client = ContainerClient.from_connection_string(conn_str, container_name)
    else:
        account_url = os.environ.get("AZURE_BLOB_ACCOUNT_URL", "").strip()
        if not account_url:
            raise RuntimeError(
                "OI_PARQUET_BLOB_* set but no AZURE_BLOB_CONNECTION_STRING / "
                "AZURE_BLOB_ACCOUNT_URL configured."
            )
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
        client = ContainerClient(account_url=account_url,
                                 container_name=container_name,
                                 credential=credential)
    return client.download_blob(blob_name).readall()


class _ParquetOiBackend:
    """Backtest backend: in-process parquet lookup."""

    def __init__(self, symbol: str) -> None:
        import pandas as pd
        path = _resolve_parquet_path(symbol)
        df = pd.read_parquet(path).sort_values("timestamp").reset_index(drop=True)
        self._ts = df["timestamp"].to_numpy(dtype="int64")
        self._oi = df["sum_oi"].to_numpy(dtype="float64")
        logger.info("[oi] parquet backend %s rows=%d range=%d..%d path=%s",
                    symbol, len(self._ts), int(self._ts[0]), int(self._ts[-1]), path)

    def value_at(self, ts_ms: int) -> float:
        idx = int(np.searchsorted(self._ts, int(ts_ms), side="right")) - 1
        if idx < 0:
            return math.nan
        return float(self._oi[idx])

    def pct_change(self, ts_ms: int, lookback_ms: int = DEFAULT_LOOKBACK_MS) -> float:
        cur = self.value_at(ts_ms)
        prev = self.value_at(int(ts_ms) - int(lookback_ms))
        if not (math.isfinite(cur) and math.isfinite(prev)) or prev <= 0:
            return math.nan
        return cur / prev - 1.0


class _RedisOiBackend:
    """Live backend: Redis ZSET reads.

    Synchronous interface to fit the strategy `on_bar` call. Uses sync `redis`
    package (not redis.asyncio) so this works inside both sync and async hosts.
    """

    def __init__(self, symbol: str, redis_url: str) -> None:
        import redis  # type: ignore
        self._symbol = symbol
        self._key = REDIS_OI_KEY_FMT.format(symbol=symbol)
        self._client = redis.from_url(
            redis_url,
            socket_connect_timeout=3,
            socket_timeout=3,
            decode_responses=True,
        )
        try:
            self._client.ping()
        except Exception as exc:  # noqa: BLE001
            logger.error("[oi] redis ping failed: %s", exc)
            raise

    def _value_at(self, ts_ms: int) -> float:
        # ZRANGEBYSCORE with limit=1, descending by score <= ts_ms
        # redis-py: zrevrangebyscore(name, max, min, start, num, withscores)
        items = self._client.zrevrangebyscore(
            self._key, max=int(ts_ms), min="-inf", start=0, num=1, withscores=True
        )
        if not items:
            return math.nan
        member, _score = items[0]
        try:
            return float(str(member).split(":", 1)[1])
        except Exception:  # noqa: BLE001
            return math.nan

    def pct_change(self, ts_ms: int, lookback_ms: int = DEFAULT_LOOKBACK_MS) -> float:
        cur = self._value_at(int(ts_ms))
        prev = self._value_at(int(ts_ms) - int(lookback_ms))
        if not (math.isfinite(cur) and math.isfinite(prev)) or prev <= 0:
            return math.nan
        return cur / prev - 1.0


_PROVIDERS: dict[tuple[str, str], object] = {}


def get_oi_provider(symbol: str, mode: Optional[str] = None) -> object:
    """Return a cached OI provider.

    mode: "backtest" or "live". When None, auto-detects from env:
      - if `OI_PROVIDER_MODE` is set, use it.
      - elif `REDIS_URL` is configured, "live".
      - else "backtest".
    """
    sym = symbol.upper()
    if mode is None:
        mode = os.environ.get("OI_PROVIDER_MODE", "").strip().lower()
        if not mode:
            mode = "live" if os.environ.get("REDIS_URL", "").strip() else "backtest"
    key = (sym, mode)
    if key in _PROVIDERS:
        return _PROVIDERS[key]
    if mode == "backtest":
        prov = _ParquetOiBackend(sym)
    elif mode == "live":
        redis_url = os.environ.get("REDIS_URL", "").strip()
        if not redis_url:
            raise RuntimeError("REDIS_URL not configured for live OI provider")
        prov = _RedisOiBackend(sym, redis_url)
    else:
        raise ValueError(f"unknown OI provider mode: {mode}")
    _PROVIDERS[key] = prov
    return prov
