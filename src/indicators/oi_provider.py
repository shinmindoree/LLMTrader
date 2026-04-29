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
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOOKBACK_MS = 24 * 3600 * 1000


class _ParquetOiBackend:
    """Backtest backend: in-process parquet lookup."""

    def __init__(self, symbol: str) -> None:
        import pandas as pd
        path = PROJECT_ROOT / PARQUET_PATH_FMT.format(symbol=symbol)
        if not path.exists():
            raise FileNotFoundError(
                f"OI parquet not found: {path}. "
                f"Run scripts/ingest_perp_meta.py to backfill."
            )
        df = pd.read_parquet(path).sort_values("timestamp").reset_index(drop=True)
        self._ts = df["timestamp"].to_numpy(dtype="int64")
        self._oi = df["sum_oi"].to_numpy(dtype="float64")
        logger.info("[oi] parquet backend %s rows=%d range=%d..%d",
                    symbol, len(self._ts), int(self._ts[0]), int(self._ts[-1]))

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
