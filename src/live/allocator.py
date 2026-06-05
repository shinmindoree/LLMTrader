"""Capital Allocator — app-level pre-trade capital gate.

Sub-accounts give us *physical* fund isolation; the allocator gives us an
extra *logical* gate that prevents a single strategy from exceeding the
budget the operator (or, later, the policy engine) granted it.

The source of truth lives in ``strategy_allocations``:

* ``allocated_usdt`` — upper bound the operator / policy set for the job.
* ``reserved_usdt`` — running tally maintained by this allocator as
  positions/orders go on and off the book.

``free_usdt = max(0, allocated - reserved)``.

Hooks plan (not in this commit — see follow-ups):

* ``live/context.py::LiveContext.buy/sell`` → reserve before placing the
  order, release on fill / cancel.
* ``live/portfolio_context.py::_SymbolTradingProxy`` → same.
* External reconciler periodically calls :meth:`sync_reserved` with the
  observed open-position notional so reservations don't drift.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control.models import StrategyAllocation
from control.repo import (
    adjust_strategy_allocation_reserved,
    get_strategy_allocation,
    reset_strategy_allocation_reserved,
)

_log = logging.getLogger("llmtrader.capital_allocator")


class CapitalAllocatorError(RuntimeError):
    """Raised on misuse of the allocator API (not on policy rejections)."""


class ReservationStatus(str, enum.Enum):  # noqa: UP042 — matches existing models.py pattern
    OK = "ok"
    CLAMPED = "clamped"      # partial reservation — granted < requested
    REJECTED = "rejected"    # no reservation made


@dataclass(slots=True)
class ReservationResult:
    """Outcome of a :meth:`CapitalAllocator.reserve` call.

    ``granted`` is the notional the caller may actually open. For
    :attr:`ReservationStatus.OK` it equals ``requested``; for
    :attr:`ReservationStatus.CLAMPED` it equals the available free budget.
    ``free_after`` is the remaining free budget *after* the reservation
    applied (useful for telemetry).
    """

    status: ReservationStatus
    requested: float
    granted: float
    free_after: float
    reason: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self.status is ReservationStatus.REJECTED


class CapitalAllocator:
    """Per-process capital allocator.

    Concurrency model: one ``asyncio.Lock`` per ``job_id`` guarantees that
    the read-modify-write sequence on ``reserved_usdt`` is atomic within
    this process. Multi-worker deployments must add a SQL-level guard
    (e.g. conditional UPDATE) — tracked in the topology plan.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession] | None = None,
        allow_clamp: bool = False,
    ) -> None:
        self._session_maker = session_maker
        self._allow_clamp_default = allow_clamp
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_registry_lock = asyncio.Lock()

    # ── public API ────────────────────────────────────────────────

    async def reserve(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        notional_usdt: float,
        allow_clamp: bool | None = None,
    ) -> ReservationResult:
        """Try to reserve ``notional_usdt`` of capital for ``job_id``.

        ``allow_clamp=True`` lets the allocator grant a smaller partial
        reservation when ``notional_usdt`` exceeds the free budget; with
        the default (or ``False``) such cases return ``REJECTED``.
        """
        if notional_usdt <= 0:
            return ReservationResult(
                status=ReservationStatus.REJECTED,
                requested=float(notional_usdt),
                granted=0.0,
                free_after=0.0,
                reason="non-positive notional",
            )

        clamp = self._allow_clamp_default if allow_clamp is None else allow_clamp
        lock = await self._lock_for(job_id)
        async with lock:
            alloc = await get_strategy_allocation(session, job_id=job_id)
            if alloc is None:
                return ReservationResult(
                    status=ReservationStatus.REJECTED,
                    requested=float(notional_usdt),
                    granted=0.0,
                    free_after=0.0,
                    reason="no strategy_allocations row",
                )

            allocated = float(alloc.allocated_usdt)
            reserved = float(alloc.reserved_usdt)
            free = max(0.0, allocated - reserved)

            if free <= 0.0:
                return ReservationResult(
                    status=ReservationStatus.REJECTED,
                    requested=float(notional_usdt),
                    granted=0.0,
                    free_after=0.0,
                    reason="no free budget",
                )

            if free + 1e-9 >= notional_usdt:
                new_reserved = await adjust_strategy_allocation_reserved(
                    session, job_id=job_id, delta_usdt=float(notional_usdt)
                )
                await session.commit()
                free_after = (
                    max(0.0, allocated - float(new_reserved))
                    if new_reserved is not None
                    else max(0.0, free - notional_usdt)
                )
                return ReservationResult(
                    status=ReservationStatus.OK,
                    requested=float(notional_usdt),
                    granted=float(notional_usdt),
                    free_after=free_after,
                )

            if not clamp:
                return ReservationResult(
                    status=ReservationStatus.REJECTED,
                    requested=float(notional_usdt),
                    granted=0.0,
                    free_after=free,
                    reason="would exceed allocation",
                )

            granted = free
            new_reserved = await adjust_strategy_allocation_reserved(
                session, job_id=job_id, delta_usdt=granted
            )
            await session.commit()
            free_after = (
                max(0.0, allocated - float(new_reserved))
                if new_reserved is not None
                else 0.0
            )
            return ReservationResult(
                status=ReservationStatus.CLAMPED,
                requested=float(notional_usdt),
                granted=granted,
                free_after=free_after,
                reason="clamped to free budget",
            )

    async def release(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        notional_usdt: float,
    ) -> float | None:
        """Release ``notional_usdt`` back to the free budget.

        Returns the new ``reserved_usdt`` value or ``None`` if no row
        exists. Negative reservations are clamped to ``0.0`` to absorb
        drift from concurrent over-releases.
        """
        if notional_usdt <= 0:
            return None
        lock = await self._lock_for(job_id)
        async with lock:
            new_reserved = await adjust_strategy_allocation_reserved(
                session,
                job_id=job_id,
                delta_usdt=-abs(float(notional_usdt)),
            )
            if new_reserved is None:
                return None
            if new_reserved < 0.0:
                _log.warning(
                    "reservation went negative for job %s (%.6f) — clamping to 0",
                    job_id,
                    new_reserved,
                )
                await reset_strategy_allocation_reserved(
                    session, job_id=job_id, reserved_usdt=0.0
                )
                new_reserved = 0.0
            await session.commit()
            return float(new_reserved)

    async def get_free(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
    ) -> float:
        alloc = await get_strategy_allocation(session, job_id=job_id)
        if alloc is None:
            return 0.0
        return max(0.0, float(alloc.allocated_usdt) - float(alloc.reserved_usdt))

    async def get_allocation(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
    ) -> StrategyAllocation | None:
        return await get_strategy_allocation(session, job_id=job_id)

    async def sync_reserved(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        reserved_usdt: float,
    ) -> None:
        """Reset ``reserved_usdt`` to a value reported by an external truth.

        Use this from the position reconciler when the on-exchange margin
        usage drifts from the allocator's running tally. Negative inputs
        are clamped to ``0.0``.
        """
        lock = await self._lock_for(job_id)
        async with lock:
            await reset_strategy_allocation_reserved(
                session,
                job_id=job_id,
                reserved_usdt=max(0.0, float(reserved_usdt)),
            )
            await session.commit()

    # ── internals ────────────────────────────────────────────────

    async def _lock_for(self, job_id: uuid.UUID) -> asyncio.Lock:
        key = str(job_id)
        async with self._lock_registry_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def forget(self, job_id: uuid.UUID) -> None:
        """Drop the in-process lock for ``job_id`` (e.g. on job teardown)."""
        self._locks.pop(str(job_id), None)


# ── module-level singleton ────────────────────────────────────────────

_allocator: CapitalAllocator | None = None


def get_capital_allocator() -> CapitalAllocator:
    """Return (and lazily create) the process-wide :class:`CapitalAllocator`."""
    global _allocator  # noqa: PLW0603 — module-level singleton accessor
    if _allocator is None:
        _allocator = CapitalAllocator()
    return _allocator


def reset_capital_allocator() -> None:
    """Drop the singleton (used by tests and graceful shutdown)."""
    global _allocator  # noqa: PLW0603 — module-level singleton accessor
    _allocator = None
