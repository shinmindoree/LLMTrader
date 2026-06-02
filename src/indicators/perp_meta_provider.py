"""Funding-rate / taker / LSR data providers (backtest + live).

Mirrors the architecture of ``oi_provider.py`` but covers three indicators
that share the same Redis ZSET shape:

  Key:    {kind}:{SYMBOL}:hist        (sorted set)
  Member: "{ts_ms}:{value}"           (string)
  Score:  ts_ms                       (int64 millis)

Indicator -> kind mapping:
  funding -> ``funding:{SYMBOL}:hist`` (cadence ~8h, /fapi/v1/fundingRate)
  taker   -> ``taker:{SYMBOL}:hist``   (cadence 5m,  /futures/data/takerlongshortRatio)
  lsr     -> ``lsr:{SYMBOL}:hist``     (cadence 5m,  /futures/data/globalLongShortAccountRatio)

Backtest backends read the same parquet files used by
``MultiFactorPortfolioStrategy._load_unified_dataset``:

  funding parquet: data/perp_meta/{SYMBOL}_funding.parquet
                   columns: funding_time (int64 ms), funding_rate (float)
  taker   parquet: data/perp_meta/{SYMBOL}_taker_5m.parquet
                   columns: timestamp (int64 ms), sum_taker_long_short_vol_ratio (float)
  lsr     parquet: data/perp_meta/{SYMBOL}_lsr_5m.parquet
                   columns: timestamp (int64 ms), count_long_short_ratio (float)

Public API:

    funding = get_funding_provider("BTCUSDT")
    funding.value_at(ts_ms)            # latest known funding rate at-or-before ts_ms
    funding.range(start_ms, end_ms)    # (ts_arr, val_arr) for [start, end] inclusive

The same shape works for ``get_taker_provider`` and ``get_lsr_provider``.
Provider mode auto-detects from environment:
    MFP_PROVIDER_MODE=backtest|live, or
    REDIS_URL / REDIS_HOST set => live, else backtest.
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("llmtrader.perp_meta_provider")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Indicator specs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _MetaSpec:
    """Static config for one indicator (funding / taker / lsr)."""
    kind: str                  # "funding" | "taker" | "lsr"
    redis_key_fmt: str         # e.g. "funding:{symbol}:hist"
    parquet_filename_fmt: str  # e.g. "{symbol}_funding.parquet"
    parquet_ts_column: str     # "funding_time" or "timestamp"
    parquet_value_column: str  # "funding_rate", "sum_taker_long_short_vol_ratio", "count_long_short_ratio"
    default_stale_tol_ms: int  # backtest stale guard


_FUNDING_SPEC = _MetaSpec(
    kind="funding",
    redis_key_fmt="funding:{symbol}:hist",
    parquet_filename_fmt="{symbol}_funding.parquet",
    parquet_ts_column="funding_time",
    parquet_value_column="funding_rate",
    # Funding only updates every ~8h, give a generous 36h window before NaN-out.
    default_stale_tol_ms=36 * 3600 * 1000,
)

_TAKER_SPEC = _MetaSpec(
    kind="taker",
    redis_key_fmt="taker:{symbol}:hist",
    parquet_filename_fmt="{symbol}_taker_5m.parquet",
    parquet_ts_column="timestamp",
    parquet_value_column="sum_taker_long_short_vol_ratio",
    default_stale_tol_ms=6 * 3600 * 1000,
)

_LSR_SPEC = _MetaSpec(
    kind="lsr",
    redis_key_fmt="lsr:{symbol}:hist",
    parquet_filename_fmt="{symbol}_lsr_5m.parquet",
    parquet_ts_column="timestamp",
    parquet_value_column="count_long_short_ratio",
    default_stale_tol_ms=6 * 3600 * 1000,
)


# ---------------------------------------------------------------------------
# Parquet path resolver (mirrors oi_provider._resolve_parquet_path)
# ---------------------------------------------------------------------------
def _cache_ttl_seconds(env_key: str) -> int:
    try:
        return int(os.environ.get(env_key, "1800"))
    except ValueError:
        return 1800


def _is_cache_fresh(cache_path: Path, env_key: str) -> bool:
    ttl = _cache_ttl_seconds(env_key)
    if ttl <= 0:
        return False
    try:
        age = time.time() - cache_path.stat().st_mtime
    except OSError:
        return False
    return age < ttl


def _resolve_parquet_path(spec: _MetaSpec, symbol: str) -> Path:
    """Locate parquet for `symbol`/`spec`. Mirrors OI resolver order:
      1. {KIND}_PARQUET_PATH_{SYMBOL} env (absolute path)
      2. <repo>/data/perp_meta/<filename>
      3. {KIND}_PARQUET_URL_{SYMBOL} env (HTTP)
      4. {KIND}_PARQUET_BLOB_CONTAINER + {KIND}_PARQUET_BLOB_NAME_{SYMBOL}
         (if the per-symbol name is unset, falls back to
         {KIND}_PARQUET_BLOB_PREFIX or perp_meta joined with the filename)
    Cached files are refreshed when older than {KIND}_PARQUET_CACHE_TTL_SEC.
    """
    sym = symbol.upper()
    kind_env = spec.kind.upper()  # FUNDING / TAKER / LSR
    fname = spec.parquet_filename_fmt.format(symbol=sym)

    explicit = os.environ.get(f"{kind_env}_PARQUET_PATH_{sym}", "").strip()
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        logger.warning("[%s] %s_PARQUET_PATH_%s set but file missing: %s",
                       spec.kind, kind_env, sym, p)

    local = PROJECT_ROOT / "data" / "perp_meta" / fname
    if local.exists():
        return local

    cache_dir = Path(os.environ.get(f"{kind_env}_PARQUET_CACHE_DIR",
                                     f"/tmp/{spec.kind}_parquet"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / fname
    ttl_env = f"{kind_env}_PARQUET_CACHE_TTL_SEC"

    url = os.environ.get(f"{kind_env}_PARQUET_URL_{sym}", "").strip()
    if url:
        if not (cache_path.exists() and _is_cache_fresh(cache_path, ttl_env)):
            logger.info("[%s] downloading %s parquet for %s from %s",
                        spec.kind, spec.kind, sym, url)
            import httpx
            tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
                resp.raise_for_status()
                with tmp_path.open("wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1 << 16):
                        f.write(chunk)
            tmp_path.replace(cache_path)
            logger.info("[%s] downloaded %d bytes -> %s",
                        spec.kind, cache_path.stat().st_size, cache_path)
        return cache_path

    container_name = os.environ.get(f"{kind_env}_PARQUET_BLOB_CONTAINER", "").strip()
    blob_name = os.environ.get(f"{kind_env}_PARQUET_BLOB_NAME_{sym}", "").strip()
    if container_name and not blob_name:
        # Convention fallback: shared prefix + canonical filename so any symbol
        # resolves without a per-symbol env var.
        prefix = os.environ.get(f"{kind_env}_PARQUET_BLOB_PREFIX", "perp_meta").strip().rstrip("/")
        blob_name = f"{prefix}/{fname}" if prefix else fname
    if container_name and blob_name:
        if not (cache_path.exists() and _is_cache_fresh(cache_path, ttl_env)):
            logger.info("[%s] downloading parquet for %s from blob %s/%s",
                        spec.kind, sym, container_name, blob_name)
            data = _download_blob(container_name, blob_name)
            tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            tmp_path.write_bytes(data)
            tmp_path.replace(cache_path)
            logger.info("[%s] downloaded %d bytes -> %s",
                        spec.kind, len(data), cache_path)
        return cache_path

    raise FileNotFoundError(
        f"{spec.kind} parquet not found for {sym}. Provide one of: "
        f"{kind_env}_PARQUET_PATH_{sym}, file at {local}, "
        f"{kind_env}_PARQUET_URL_{sym}, or "
        f"{kind_env}_PARQUET_BLOB_CONTAINER + {kind_env}_PARQUET_BLOB_NAME_{sym}."
    )


def _download_blob(container_name: str, blob_name: str) -> bytes:
    """Same auth chain as oi_provider._download_blob."""
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    from azure.storage.blob import ContainerClient
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "").strip()
    if conn_str:
        client = ContainerClient.from_connection_string(conn_str, container_name)
    else:
        account_url = os.environ.get("AZURE_BLOB_ACCOUNT_URL", "").strip()
        if not account_url:
            raise RuntimeError(
                "BLOB resolver invoked but neither AZURE_BLOB_CONNECTION_STRING "
                "nor AZURE_BLOB_ACCOUNT_URL is set."
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


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class _MetaProviderBase:
    """Read-only at-or-before lookup over a (ts_ms, value) series."""
    spec: _MetaSpec
    symbol: str

    def value_at(self, ts_ms: int) -> float:
        raise NotImplementedError

    def range(self, start_ms: int, end_ms: int) -> tuple[np.ndarray, np.ndarray]:
        """Return (ts_arr, val_arr) for entries with ``start_ms <= ts <= end_ms``."""
        raise NotImplementedError


class _ParquetMetaBackend(_MetaProviderBase):
    """Backtest backend: in-process parquet lookup."""

    def __init__(self, spec: _MetaSpec, symbol: str) -> None:
        import pandas as pd
        path = _resolve_parquet_path(spec, symbol)
        df = pd.read_parquet(path)
        df = df.sort_values(spec.parquet_ts_column).reset_index(drop=True)
        self.spec = spec
        self.symbol = symbol
        self._ts = df[spec.parquet_ts_column].to_numpy(dtype="int64")
        self._val = df[spec.parquet_value_column].to_numpy(dtype="float64")
        self._source_path = path
        try:
            st = path.stat()
            self._source_mtime_ns = int(st.st_mtime_ns)
            self._source_size = int(st.st_size)
        except OSError:
            self._source_mtime_ns = 0
            self._source_size = 0
        kind_env = spec.kind.upper()
        try:
            self._stale_tol_ms = int(os.environ.get(
                f"{kind_env}_PARQUET_STALE_TOLERANCE_MS",
                str(spec.default_stale_tol_ms),
            ))
        except ValueError:
            self._stale_tol_ms = spec.default_stale_tol_ms
        logger.info(
            "[%s] parquet backend %s rows=%d range=%d..%d path=%s stale_tol_ms=%d",
            spec.kind, symbol, len(self._ts),
            int(self._ts[0]) if len(self._ts) else 0,
            int(self._ts[-1]) if len(self._ts) else 0,
            path, self._stale_tol_ms,
        )

    def value_at(self, ts_ms: int) -> float:
        ts = int(ts_ms)
        if len(self._ts) and ts > int(self._ts[-1]) + self._stale_tol_ms:
            return math.nan
        idx = int(np.searchsorted(self._ts, ts, side="right")) - 1
        if idx < 0:
            return math.nan
        return float(self._val[idx])

    def range(self, start_ms: int, end_ms: int) -> tuple[np.ndarray, np.ndarray]:
        if len(self._ts) == 0 or start_ms > end_ms:
            return np.empty(0, dtype="int64"), np.empty(0, dtype="float64")
        lo = int(np.searchsorted(self._ts, int(start_ms), side="left"))
        hi = int(np.searchsorted(self._ts, int(end_ms), side="right"))
        return self._ts[lo:hi].copy(), self._val[lo:hi].copy()


class _RedisMetaBackend(_MetaProviderBase):
    """Live backend: Redis ZSET reads."""

    def __init__(
        self,
        spec: _MetaSpec,
        symbol: str,
        *,
        redis_url: str = "",
        redis_host: str = "",
        redis_username: str = "",
        redis_password: str = "",
    ) -> None:
        from common.redis_client import (
            create_redis_client,
            create_redis_client_from_parts,
            create_redis_client_with_aad,
        )

        self.spec = spec
        self.symbol = symbol
        self._key = spec.redis_key_fmt.format(symbol=symbol)
        if redis_host and redis_username:
            self._client = create_redis_client_with_aad(
                host=redis_host,
                username=redis_username,
                port=int(os.environ.get("REDIS_PORT", "6380")),
                ssl=os.environ.get("REDIS_SSL", "true").strip().lower() != "false",
                socket_connect_timeout=3,
                socket_timeout=3,
                decode_responses=True,
            )
        elif redis_host and redis_password:
            self._client = create_redis_client_from_parts(
                host=redis_host,
                port=int(os.environ.get("REDIS_PORT", "6380")),
                password=redis_password,
                ssl=os.environ.get("REDIS_SSL", "true").strip().lower() != "false",
                socket_connect_timeout=3,
                socket_timeout=3,
                decode_responses=True,
            )
        else:
            self._client = create_redis_client(
                redis_url,
                socket_connect_timeout=3,
                socket_timeout=3,
                decode_responses=True,
            )
        try:
            self._client.ping()
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] redis ping failed: %s", spec.kind, exc)
            raise

    @staticmethod
    def _parse_member(member: object) -> tuple[int, float] | None:
        try:
            ts_str, val_str = str(member).split(":", 1)
            return int(ts_str), float(val_str)
        except Exception:  # noqa: BLE001
            return None

    def value_at(self, ts_ms: int) -> float:
        items = self._client.zrevrangebyscore(
            self._key, max=int(ts_ms), min="-inf", start=0, num=1, withscores=True
        )
        if not items:
            return math.nan
        parsed = self._parse_member(items[0][0])
        return parsed[1] if parsed is not None else math.nan

    def range(self, start_ms: int, end_ms: int) -> tuple[np.ndarray, np.ndarray]:
        items = self._client.zrangebyscore(
            self._key,
            min=int(start_ms),
            max=int(end_ms),
            withscores=True,
        )
        if not items:
            return np.empty(0, dtype="int64"), np.empty(0, dtype="float64")
        ts_list: list[int] = []
        val_list: list[float] = []
        for member, _score in items:
            parsed = self._parse_member(member)
            if parsed is None:
                continue
            ts_list.append(parsed[0])
            val_list.append(parsed[1])
        return (np.asarray(ts_list, dtype="int64"),
                np.asarray(val_list, dtype="float64"))


# ---------------------------------------------------------------------------
# Factory cache
# ---------------------------------------------------------------------------
_PROVIDERS: dict[tuple[str, str, str], _MetaProviderBase] = {}


def _detect_mode(env_key: str = "MFP_PROVIDER_MODE") -> str:
    mode = os.environ.get(env_key, "").strip().lower()
    if mode:
        return mode
    if (os.environ.get("REDIS_URL", "").strip()
            or os.environ.get("REDIS_HOST", "").strip()):
        return "live"
    return "backtest"


def _get(spec: _MetaSpec, symbol: str, mode: Optional[str]) -> _MetaProviderBase:
    sym = symbol.upper()
    if mode is None:
        mode = _detect_mode()
    key = (spec.kind, sym, mode)
    cached = _PROVIDERS.get(key)
    if cached is not None and mode == "backtest" and isinstance(cached, _ParquetMetaBackend):
        # Drop stale cache when underlying parquet was refreshed on disk.
        try:
            fresh = _resolve_parquet_path(spec, sym)
            stat = fresh.stat()
            if (int(stat.st_mtime_ns) != cached._source_mtime_ns
                    or int(stat.st_size) != cached._source_size):
                logger.info("[%s] parquet changed on disk; rebuilding backend for %s",
                            spec.kind, sym)
                _PROVIDERS.pop(key, None)
                cached = None
        except OSError as exc:  # noqa: BLE001
            logger.warning("[%s] stat failed during freshness check: %s",
                           spec.kind, exc)
    if cached is not None:
        return cached
    if mode == "backtest":
        prov: _MetaProviderBase = _ParquetMetaBackend(spec, sym)
    elif mode == "live":
        redis_url = os.environ.get("REDIS_URL", "").strip()
        redis_host = os.environ.get("REDIS_HOST", "").strip()
        redis_username = os.environ.get("REDIS_USERNAME", "").strip()
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        if not redis_url and not (redis_host and (redis_username or redis_password)):
            raise RuntimeError(
                "REDIS_URL, REDIS_HOST+REDIS_USERNAME, or REDIS_HOST+REDIS_PASSWORD "
                f"is required for live {spec.kind} provider"
            )
        prov = _RedisMetaBackend(
            spec,
            sym,
            redis_url=redis_url,
            redis_host=redis_host,
            redis_username=redis_username,
            redis_password=redis_password,
        )
    else:
        raise ValueError(f"unknown provider mode: {mode}")
    _PROVIDERS[key] = prov
    return prov


def get_funding_provider(symbol: str, mode: Optional[str] = None) -> _MetaProviderBase:
    return _get(_FUNDING_SPEC, symbol, mode)


def get_taker_provider(symbol: str, mode: Optional[str] = None) -> _MetaProviderBase:
    return _get(_TAKER_SPEC, symbol, mode)


def get_lsr_provider(symbol: str, mode: Optional[str] = None) -> _MetaProviderBase:
    return _get(_LSR_SPEC, symbol, mode)


def reset_provider_cache() -> None:
    """Force recreation of all cached providers (mainly for tests)."""
    _PROVIDERS.clear()
