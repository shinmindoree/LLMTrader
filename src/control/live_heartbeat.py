"""Redis-backed liveness heartbeat for LIVE runner jobs.

Decouples runner liveness from the PostgreSQL/PgBouncer write path so that
churn on the API layer (DB pool pressure during test-api restarts) cannot
falsely mark a healthy runner as stale.

Design:
- Key:   live:hb:{job_id}
- Value: ISO-8601 timestamp (string)
- TTL:   stale_seconds (default 60s)
- Writer: live_executor._heartbeat_loop calls ``mark_alive`` every interval.
- Reader: worker._periodic_stale_live_reconcile calls ``is_alive`` before
          requeuing a job. If Redis is unavailable, both functions degrade
          to the legacy DB-only behaviour (returns ``None``).

Why a separate module?
- Importing redis lazily prevents the runner from failing to import when
  Redis is not configured (e.g. in unit tests).
- Errors are swallowed and surfaced as ``None`` so callers can fall back to
  the existing DB heartbeat path without a hard dependency on Redis.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from settings import get_settings

logger = logging.getLogger(__name__)

_redis_client: Any | None = None
_redis_init_attempted = False
_redis_lock = asyncio.Lock()


def _key(job_id: uuid.UUID) -> str:
    return f"live:hb:{job_id}"


async def _get_redis() -> Any | None:
    """Lazy-init async Redis client. Returns ``None`` if unavailable."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    async with _redis_lock:
        if _redis_init_attempted:
            return _redis_client
        _redis_init_attempted = True
        settings = get_settings()
        if not settings.redis.is_configured:
            logger.info("[live-heartbeat] Redis not configured; heartbeat falls back to DB only")
            return None
        try:
            from common.redis_client import (
                create_async_redis_client,
                create_async_redis_client_with_aad,
            )

            if settings.redis.is_aad_configured:
                _redis_client = create_async_redis_client_with_aad(
                    host=settings.redis.host,
                    username=settings.redis.username,
                    port=settings.redis.port,
                    ssl=settings.redis.ssl,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                )
            else:
                _redis_client = create_async_redis_client(
                    settings.redis.url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                )
            await _redis_client.ping()
            logger.info("[live-heartbeat] Redis heartbeat connected")
        except Exception:
            logger.warning("[live-heartbeat] Redis connection failed; falling back to DB only", exc_info=True)
            _redis_client = None
    return _redis_client


async def mark_alive(job_id: uuid.UUID, *, ttl_seconds: int) -> bool:
    """Write a heartbeat marker to Redis with TTL ``ttl_seconds``.

    Returns ``True`` on success, ``False`` if Redis is unavailable or write
    failed. Never raises.
    """
    r = await _get_redis()
    if r is None:
        return False
    try:
        await asyncio.wait_for(
            r.set(_key(job_id), datetime.now().isoformat(), ex=max(15, int(ttl_seconds))),
            timeout=2.0,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[live-heartbeat] mark_alive failed job_id=%s: %s: %s", job_id, type(exc).__name__, exc)
        return False


async def is_alive(job_id: uuid.UUID) -> bool | None:
    """Check whether a heartbeat key exists for ``job_id``.

    Returns:
        - ``True``  → heartbeat key present (job confirmed alive)
        - ``False`` → heartbeat key absent (job possibly stale)
        - ``None``  → Redis unavailable / transient error → caller should
                      fall back to the DB heartbeat column.
    """
    r = await _get_redis()
    if r is None:
        return None
    try:
        result = await asyncio.wait_for(r.exists(_key(job_id)), timeout=1.5)
        return bool(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[live-heartbeat] is_alive failed job_id=%s: %s: %s", job_id, type(exc).__name__, exc)
        return None


async def clear_alive(job_id: uuid.UUID) -> None:
    """Remove the heartbeat marker (used on graceful job termination)."""
    r = await _get_redis()
    if r is None:
        return
    try:
        await asyncio.wait_for(r.delete(_key(job_id)), timeout=1.5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[live-heartbeat] clear_alive failed job_id=%s: %s: %s", job_id, type(exc).__name__, exc)
