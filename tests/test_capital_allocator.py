"""Unit tests for ``live.allocator.CapitalAllocator``.

We don't spin up a Postgres for this — the allocator's repo calls are
monkeypatched to a tiny in-memory store. That keeps the test focused on
the decision logic (reject / clamp / grant, lock serialisation, drift
clamping) which is the part most likely to regress.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from live import allocator as allocator_mod
from live.allocator import (
    CapitalAllocator,
    ReservationStatus,
    get_capital_allocator,
    reset_capital_allocator,
)

# ── tiny in-memory repo ──────────────────────────────────────────────


@dataclass
class _FakeAllocation:
    job_id: uuid.UUID
    allocated_usdt: float
    reserved_usdt: float


class _Store:
    """Stand-in for the rows in ``strategy_allocations``."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, _FakeAllocation] = {}

    def add(self, job_id: uuid.UUID, allocated: float, reserved: float = 0.0) -> None:
        self.rows[job_id] = _FakeAllocation(
            job_id=job_id, allocated_usdt=allocated, reserved_usdt=reserved
        )


class _FakeSession:
    """Just enough of ``AsyncSession`` for the allocator to call .commit()."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    reset_capital_allocator()
    s = _Store()

    async def fake_get(_session, *, job_id: uuid.UUID):  # noqa: ANN001 — match signature
        return s.rows.get(job_id)

    async def fake_adjust(
        _session, *, job_id: uuid.UUID, delta_usdt: float,
    ) -> float | None:
        row = s.rows.get(job_id)
        if row is None:
            return None
        row.reserved_usdt = row.reserved_usdt + float(delta_usdt)
        return row.reserved_usdt

    async def fake_reset(
        _session, *, job_id: uuid.UUID, reserved_usdt: float,
    ) -> None:
        row = s.rows.get(job_id)
        if row is None:
            return
        row.reserved_usdt = max(0.0, float(reserved_usdt))

    monkeypatch.setattr(allocator_mod, "get_strategy_allocation", fake_get)
    monkeypatch.setattr(allocator_mod, "adjust_strategy_allocation_reserved", fake_adjust)
    monkeypatch.setattr(allocator_mod, "reset_strategy_allocation_reserved", fake_reset)
    return s


def _new_job_id() -> uuid.UUID:
    return uuid.uuid4()


# ── reserve ──────────────────────────────────────────────────────────


class TestReserve:
    @pytest.mark.asyncio
    async def test_grants_when_within_budget(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=1000.0)

        alloc = CapitalAllocator()
        session = _FakeSession()

        res = await alloc.reserve(session, job_id=job_id, notional_usdt=200.0)

        assert res.status is ReservationStatus.OK
        assert res.granted == 200.0
        assert res.free_after == pytest.approx(800.0)
        assert store.rows[job_id].reserved_usdt == pytest.approx(200.0)
        assert session.commits == 1

    @pytest.mark.asyncio
    async def test_rejects_when_no_row(self) -> None:
        # store fixture not used here on purpose — bare allocator with no rows
        reset_capital_allocator()

        async def fake_get(_session, *, job_id):  # noqa: ANN001
            return None

        async def fake_adjust(*_a, **_k):
            raise AssertionError("must not be called when row missing")

        async def fake_reset(*_a, **_k):
            raise AssertionError("must not be called when row missing")

        with (
            patch.object(allocator_mod, "get_strategy_allocation", fake_get),
            patch.object(allocator_mod, "adjust_strategy_allocation_reserved", fake_adjust),
            patch.object(allocator_mod, "reset_strategy_allocation_reserved", fake_reset),
        ):
            res = await CapitalAllocator().reserve(
                _FakeSession(), job_id=_new_job_id(), notional_usdt=50.0,
            )

        assert res.status is ReservationStatus.REJECTED
        assert res.granted == 0.0
        assert res.reason == "no strategy_allocations row"

    @pytest.mark.asyncio
    async def test_rejects_when_no_free_budget(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=500.0, reserved=500.0)

        res = await CapitalAllocator().reserve(
            _FakeSession(), job_id=job_id, notional_usdt=1.0,
        )

        assert res.status is ReservationStatus.REJECTED
        assert res.reason == "no free budget"
        assert store.rows[job_id].reserved_usdt == 500.0

    @pytest.mark.asyncio
    async def test_rejects_non_positive_notional(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=500.0)

        for amt in (0.0, -10.0):
            res = await CapitalAllocator().reserve(
                _FakeSession(), job_id=job_id, notional_usdt=amt,
            )
            assert res.status is ReservationStatus.REJECTED
            assert res.reason == "non-positive notional"

        assert store.rows[job_id].reserved_usdt == 0.0

    @pytest.mark.asyncio
    async def test_rejects_over_budget_without_clamp(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=100.0)

        res = await CapitalAllocator().reserve(
            _FakeSession(), job_id=job_id, notional_usdt=250.0,
        )

        assert res.status is ReservationStatus.REJECTED
        assert res.reason == "would exceed allocation"
        assert res.free_after == pytest.approx(100.0)
        assert store.rows[job_id].reserved_usdt == 0.0

    @pytest.mark.asyncio
    async def test_clamps_when_allowed(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=100.0, reserved=30.0)

        res = await CapitalAllocator().reserve(
            _FakeSession(), job_id=job_id, notional_usdt=250.0, allow_clamp=True,
        )

        assert res.status is ReservationStatus.CLAMPED
        assert res.granted == pytest.approx(70.0)  # free = 100 - 30
        assert res.free_after == pytest.approx(0.0)
        assert store.rows[job_id].reserved_usdt == pytest.approx(100.0)
        assert res.reason == "clamped to free budget"

    @pytest.mark.asyncio
    async def test_clamp_default_via_constructor(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=100.0)

        alloc = CapitalAllocator(allow_clamp=True)
        res = await alloc.reserve(_FakeSession(), job_id=job_id, notional_usdt=999.0)

        assert res.status is ReservationStatus.CLAMPED
        assert res.granted == pytest.approx(100.0)


# ── release ──────────────────────────────────────────────────────────


class TestRelease:
    @pytest.mark.asyncio
    async def test_release_reduces_reserved(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=500.0, reserved=200.0)

        new_reserved = await CapitalAllocator().release(
            _FakeSession(), job_id=job_id, notional_usdt=80.0,
        )

        assert new_reserved == pytest.approx(120.0)
        assert store.rows[job_id].reserved_usdt == pytest.approx(120.0)

    @pytest.mark.asyncio
    async def test_release_clamps_negative_drift_to_zero(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=500.0, reserved=50.0)

        new_reserved = await CapitalAllocator().release(
            _FakeSession(), job_id=job_id, notional_usdt=999.0,
        )

        assert new_reserved == 0.0
        assert store.rows[job_id].reserved_usdt == 0.0

    @pytest.mark.asyncio
    async def test_release_no_op_for_non_positive(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=500.0, reserved=200.0)

        for amt in (0.0, -10.0):
            assert (
                await CapitalAllocator().release(
                    _FakeSession(), job_id=job_id, notional_usdt=amt,
                )
                is None
            )
        # Untouched.
        assert store.rows[job_id].reserved_usdt == pytest.approx(200.0)

    @pytest.mark.asyncio
    async def test_release_missing_row_returns_none(self) -> None:
        async def fake_get(*_a, **_k):
            return None

        async def fake_adjust(*_a, **_k):
            return None

        async def fake_reset(*_a, **_k):
            return None

        reset_capital_allocator()
        with (
            patch.object(allocator_mod, "get_strategy_allocation", fake_get),
            patch.object(allocator_mod, "adjust_strategy_allocation_reserved", fake_adjust),
            patch.object(allocator_mod, "reset_strategy_allocation_reserved", fake_reset),
        ):
            res = await CapitalAllocator().release(
                _FakeSession(), job_id=_new_job_id(), notional_usdt=50.0,
            )
        assert res is None


# ── get_free / sync_reserved ─────────────────────────────────────────


class TestQueriesAndSync:
    @pytest.mark.asyncio
    async def test_get_free_reflects_reserved(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=300.0, reserved=120.0)

        free = await CapitalAllocator().get_free(_FakeSession(), job_id=job_id)
        assert free == pytest.approx(180.0)

    @pytest.mark.asyncio
    async def test_get_free_clamps_overflow(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=100.0, reserved=180.0)
        free = await CapitalAllocator().get_free(_FakeSession(), job_id=job_id)
        assert free == 0.0

    @pytest.mark.asyncio
    async def test_get_free_missing_row_is_zero(self) -> None:
        async def fake_get(*_a, **_k):
            return None

        async def fake_adjust(*_a, **_k):
            return None

        async def fake_reset(*_a, **_k):
            return None

        with (
            patch.object(allocator_mod, "get_strategy_allocation", fake_get),
            patch.object(allocator_mod, "adjust_strategy_allocation_reserved", fake_adjust),
            patch.object(allocator_mod, "reset_strategy_allocation_reserved", fake_reset),
        ):
            free = await CapitalAllocator().get_free(
                _FakeSession(), job_id=_new_job_id(),
            )
        assert free == 0.0

    @pytest.mark.asyncio
    async def test_sync_reserved_clamps_negative(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=500.0, reserved=200.0)

        await CapitalAllocator().sync_reserved(
            _FakeSession(), job_id=job_id, reserved_usdt=-50.0,
        )
        assert store.rows[job_id].reserved_usdt == 0.0

    @pytest.mark.asyncio
    async def test_sync_reserved_normal_path(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=500.0, reserved=200.0)

        await CapitalAllocator().sync_reserved(
            _FakeSession(), job_id=job_id, reserved_usdt=125.5,
        )
        assert store.rows[job_id].reserved_usdt == pytest.approx(125.5)


# ── concurrency ──────────────────────────────────────────────────────


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_per_job_lock_serialises_reservations(self, store: _Store) -> None:
        """Two concurrent reservations on the same job must not both
        succeed when their sum exceeds the budget."""
        job_id = _new_job_id()
        store.add(job_id, allocated=100.0)

        alloc = CapitalAllocator()
        results = await asyncio.gather(
            alloc.reserve(_FakeSession(), job_id=job_id, notional_usdt=60.0),
            alloc.reserve(_FakeSession(), job_id=job_id, notional_usdt=60.0),
        )

        statuses = sorted(r.status for r in results)
        assert statuses == sorted(
            [ReservationStatus.OK, ReservationStatus.REJECTED],
            key=lambda s: s.value,
        )
        # Only the first reservation should have stuck.
        assert store.rows[job_id].reserved_usdt == pytest.approx(60.0)

    @pytest.mark.asyncio
    async def test_forget_drops_lock(self, store: _Store) -> None:
        job_id = _new_job_id()
        store.add(job_id, allocated=10.0)

        alloc = CapitalAllocator()
        await alloc.reserve(_FakeSession(), job_id=job_id, notional_usdt=1.0)
        assert str(job_id) in alloc._locks  # noqa: SLF001 — testing the housekeeping
        alloc.forget(job_id)
        assert str(job_id) not in alloc._locks  # noqa: SLF001


# ── singleton ────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_capital_allocator_is_cached(self) -> None:
        reset_capital_allocator()
        a1 = get_capital_allocator()
        a2 = get_capital_allocator()
        assert a1 is a2

    def test_reset_clears_singleton(self) -> None:
        reset_capital_allocator()
        a1 = get_capital_allocator()
        reset_capital_allocator()
        a2 = get_capital_allocator()
        assert a1 is not a2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
