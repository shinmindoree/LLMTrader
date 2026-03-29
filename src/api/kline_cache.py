"""Redis-backed kline (candle) cache for quick backtest.

Caches Binance kline data with TTL to protect against rate limits
and reduce response times for repeated requests.
Falls back to direct Binance API calls if Redis is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import msgpack

from settings import get_settings

logger = logging.getLogger(__name__)

_redis_client: Any | None = None
_redis_init_attempted = False
_redis_lock = asyncio.Lock()


async def _get_redis() -> Any | None:
    """Lazy-init Redis connection. Returns None if unavailable."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    async with _redis_lock:
        if _redis_init_attempted:
            return _redis_client
        _redis_init_attempted = True
        settings = get_settings()
        if not settings.redis.is_configured:
            logger.info("Redis not configured; kline cache disabled")
            return None
        try:
            import redis.asyncio as aioredis

            _redis_client = aioredis.from_url(
                settings.redis.url,
                decode_responses=False,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            await _redis_client.ping()
            logger.info("Redis kline cache connected")
        except Exception:
            logger.warning("Redis connection failed; kline cache disabled", exc_info=True)
            _redis_client = None
    return _redis_client


def _cache_key(symbol: str, interval: str, start_ts: int, end_ts: int) -> str:
    return f"kline:{symbol}:{interval}:{start_ts}:{end_ts}"


async def get_cached_klines(
    symbol: str, interval: str, start_ts: int, end_ts: int
) -> list[list[Any]] | None:
    """Try to fetch klines from Redis cache. Returns None on miss or error."""
    r = await _get_redis()
    if r is None:
        return None
    key = _cache_key(symbol, interval, start_ts, end_ts)
    try:
        data = await r.get(key)
        if data is None:
            return None
        return msgpack.unpackb(data, raw=False)
    except Exception:
        logger.warning("Redis cache read error", exc_info=True)
        return None


async def set_cached_klines(
    symbol: str,
    interval: str,
    start_ts: int,
    end_ts: int,
    klines: list[list[Any]],
) -> None:
    """Store klines in Redis cache with TTL."""
    r = await _get_redis()
    if r is None:
        return
    key = _cache_key(symbol, interval, start_ts, end_ts)
    ttl = get_settings().redis.kline_cache_ttl
    try:
        packed = msgpack.packb(klines, use_bin_type=True)
        await r.set(key, packed, ex=ttl)
    except Exception:
        logger.warning("Redis cache write error", exc_info=True)


# ── Quota helpers (Redis INCR) ──────────────────────────────


async def get_daily_quota_count(user_id: str) -> int | None:
    """Get today's quick backtest count. Returns None if Redis unavailable."""
    r = await _get_redis()
    if r is None:
        return None
    from datetime import datetime, timezone

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"quota:quick_bt:{user_id}:{date_str}"
    try:
        val = await r.get(key)
        return int(val) if val is not None else 0
    except Exception:
        logger.warning("Redis quota read error", exc_info=True)
        return None


async def increment_daily_quota(user_id: str) -> int | None:
    """Increment and return today's quick backtest count. Returns None if Redis unavailable."""
    r = await _get_redis()
    if r is None:
        return None
    from datetime import datetime, timezone

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"quota:quick_bt:{user_id}:{date_str}"
    try:
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 86400)
        return int(count)
    except Exception:
        logger.warning("Redis quota increment error", exc_info=True)
        return None
