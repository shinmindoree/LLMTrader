"""Redis-backed persistence for strategy in-memory state.

Lets a strategy survive a container restart without re-running its full
warmup replay. For MFP this is leg-side / entry-price / entry-TF-timestamp;
other strategies can use the same mechanism by passing a JSON-serialisable
dict as the snapshot payload.

Design (mirrors ``live_heartbeat``):
- Key:   ``strategy:state:{job_id}``
- Value: JSON-encoded snapshot blob
- TTL:   default 7 days (``STRATEGY_STATE_TTL_SECONDS``)
- Lazy-imports the redis client; degrades to no-op if Redis is unavailable.
- Errors are swallowed and surfaced as ``None`` / ``False`` so callers can
  fall back to a full warmup without a hard dependency on Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from settings import get_settings

logger = logging.getLogger(__name__)

STRATEGY_STATE_TTL_SECONDS = 7 * 24 * 3600  # 7 days

_redis_client: Any | None = None
_redis_init_attempted = False
_redis_lock = asyncio.Lock()


def _key(job_id: uuid.UUID | str) -> str:
    return f"strategy:state:{job_id}"


async def _get_redis() -> Any | None:
    """Lazy-init async Redis client. Returns ``None`` if unavailable.

    Uses the same configuration as ``live_heartbeat`` (AAD or URL), but
    maintains a separate client instance so each subsystem has its own
    connection pool and a failure of one (e.g. transient state-save
    timeout) cannot starve the heartbeat path.
    """
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    async with _redis_lock:
        if _redis_init_attempted:
            return _redis_client
        _redis_init_attempted = True
        settings = get_settings()
        if not settings.redis.is_configured:
            logger.info("[strategy-state] Redis not configured; state persistence disabled")
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
            logger.info("[strategy-state] Redis state client connected")
        except Exception:
            logger.warning(
                "[strategy-state] Redis connection failed; state persistence disabled",
                exc_info=True,
            )
            _redis_client = None
    return _redis_client


async def save_state(
    job_id: uuid.UUID | str,
    state: dict[str, Any],
    *,
    ttl_seconds: int = STRATEGY_STATE_TTL_SECONDS,
) -> bool:
    """JSON-encode ``state`` and write to Redis with ``ttl_seconds`` TTL.

    Returns ``True`` on success, ``False`` if Redis is unavailable or the
    write failed. Never raises.
    """
    r = await _get_redis()
    if r is None:
        return False
    try:
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[strategy-state] state is not JSON-serialisable job_id=%s: %s: %s",
            job_id,
            type(exc).__name__,
            exc,
        )
        return False
    try:
        await asyncio.wait_for(
            r.set(_key(job_id), payload, ex=max(60, int(ttl_seconds))),
            timeout=2.0,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[strategy-state] save_state failed job_id=%s: %s: %s",
            job_id,
            type(exc).__name__,
            exc,
        )
        return False


async def load_state(job_id: uuid.UUID | str) -> dict[str, Any] | None:
    """Read a previously-saved snapshot for ``job_id``.

    Returns ``None`` if Redis is unavailable, the key is missing, or the
    stored value cannot be decoded.
    """
    r = await _get_redis()
    if r is None:
        return None
    try:
        raw = await asyncio.wait_for(r.get(_key(job_id)), timeout=1.5)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[strategy-state] load_state failed job_id=%s: %s: %s",
            job_id,
            type(exc).__name__,
            exc,
        )
        return None
    if raw is None:
        return None
    try:
        result = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[strategy-state] load_state JSON decode failed job_id=%s: %s: %s",
            job_id,
            type(exc).__name__,
            exc,
        )
        return None
    if not isinstance(result, dict):
        return None
    return result


async def clear_state(job_id: uuid.UUID | str) -> None:
    """Remove the snapshot (used on graceful job termination)."""
    r = await _get_redis()
    if r is None:
        return
    try:
        await asyncio.wait_for(r.delete(_key(job_id)), timeout=1.5)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[strategy-state] clear_state failed job_id=%s: %s: %s",
            job_id,
            type(exc).__name__,
            exc,
        )
