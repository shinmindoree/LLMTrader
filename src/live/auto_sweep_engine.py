"""Backwards-compatibility shim for the old auto-sweep engine.

The auto-sweep loop now lives inside :mod:`live.capital_router` so it
can share the audited transfer/idempotency layer with the rest of the
sub-account topology. This module preserves the import surface that
``api.main`` (and any external callers) used to consume so the cutover
is invisible.

Every name below delegates to its capital-router counterpart:

* :func:`start_engine` → :func:`capital_router.start_capital_router`
* :func:`stop_engine` → :func:`capital_router.stop_capital_router`
* :func:`trigger_user_sweep` → :func:`capital_router.trigger_user_cycle`
* :func:`get_user_status` → :func:`capital_router.get_user_status`
* :func:`snapshot_key` → :func:`capital_router._snapshot_key`
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from live.capital_router import (
    _snapshot_key,
    start_capital_router,
    stop_capital_router,
    trigger_user_cycle,
)
from live.capital_router import (
    get_user_status as _get_user_status,
)


def snapshot_key(user_id: str) -> str:
    return _snapshot_key(user_id)


async def start_engine(session_maker: async_sessionmaker[AsyncSession]) -> None:
    await start_capital_router(session_maker)


async def stop_engine() -> None:
    await stop_capital_router()


async def trigger_user_sweep(
    session_maker: async_sessionmaker[AsyncSession], *, user_id: str
) -> None:
    await trigger_user_cycle(session_maker, user_id=user_id)


async def get_user_status(
    session: AsyncSession, *, user_id: str
) -> dict[str, Any] | None:
    return await _get_user_status(session, user_id=user_id)


__all__ = [
    "get_user_status",
    "snapshot_key",
    "start_engine",
    "stop_engine",
    "trigger_user_sweep",
]
