from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from control.enums import EventKind, JobStatus, JobType
from control.models import Job, JobEvent, Order, Trade

ACTIVE_STATUSES = {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.STOP_REQUESTED}
FINISHED_STATUSES = {JobStatus.SUCCEEDED, JobStatus.STOPPED, JobStatus.FAILED}
ACTIVE_STATUS_VALUES = {str(s) for s in ACTIVE_STATUSES}


async def create_job(
    session: AsyncSession,
    *,
    job_type: JobType,
    strategy_path: str,
    config_json: dict[str, Any],
) -> Job:
    job = Job(type=job_type, status=JobStatus.PENDING, strategy_path=strategy_path, config_json=config_json)
    session.add(job)
    await session.flush()
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await session.execute(select(Job).where(Job.job_id == job_id))
    return result.scalar_one_or_none()


async def list_jobs(
    session: AsyncSession,
    *,
    limit: int = 50,
    job_type: JobType | None = None,
) -> list[Job]:
    stmt: Select[tuple[Job]] = select(Job)
    if job_type is not None:
        stmt = stmt.where(Job.type == job_type)
    result = await session.execute(stmt.order_by(Job.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def delete_job(session: AsyncSession, job_id: uuid.UUID) -> tuple[bool, JobStatus | None]:
    job = await get_job(session, job_id)
    if not job:
        return False, None
    status_value = str(job.status)
    if status_value in ACTIVE_STATUS_VALUES:
        return False, JobStatus(status_value)
    await session.execute(delete(Job).where(Job.job_id == job_id))
    return True, JobStatus(status_value)


async def delete_jobs(
    session: AsyncSession,
    *,
    job_type: JobType | None = None,
) -> dict[str, int]:
    active_stmt = select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE_STATUSES))
    if job_type is not None:
        active_stmt = active_stmt.where(Job.type == job_type)
    active_count = int((await session.execute(active_stmt)).scalar_one() or 0)

    delete_stmt = delete(Job).where(Job.status.in_(FINISHED_STATUSES))
    if job_type is not None:
        delete_stmt = delete_stmt.where(Job.type == job_type)
    res = await session.execute(delete_stmt)
    return {
        "deleted": int(res.rowcount or 0),
        "skipped_active": active_count,
    }


async def finalize_orphaned_jobs(session: AsyncSession, *, reason: str = "runner_restart") -> dict[str, int]:
    """Finalize jobs that were left RUNNING/STOP_REQUESTED but have no active runner.

    This is primarily used on runner startup to clean up stale rows after crashes/restarts.
    """
    now = datetime.now()

    running_ids = list(
        (
            await session.execute(
                select(Job.job_id)
                .where(Job.ended_at.is_(None))
                .where(Job.status == JobStatus.RUNNING)
            )
        )
        .scalars()
        .all()
    )
    stop_requested_ids = list(
        (
            await session.execute(
                select(Job.job_id)
                .where(Job.ended_at.is_(None))
                .where(Job.status == JobStatus.STOP_REQUESTED)
            )
        )
        .scalars()
        .all()
    )

    res_running = None
    if running_ids:
        res_running = await session.execute(
            update(Job)
            .where(Job.job_id.in_(running_ids))
            .values(status=JobStatus.FAILED, ended_at=now, updated_at=now, error=f"Orphaned job ({reason})")
        )

    res_stop_requested = None
    if stop_requested_ids:
        res_stop_requested = await session.execute(
            update(Job)
            .where(Job.job_id.in_(stop_requested_ids))
            .values(status=JobStatus.STOPPED, ended_at=now, updated_at=now)
        )

    for job_id in running_ids:
        await append_event(
            session,
            job_id=job_id,
            kind=EventKind.STATUS,
            message="JOB_FAILED",
            payload_json={"error": f"Orphaned job ({reason})"},
        )
    for job_id in stop_requested_ids:
        await append_event(
            session,
            job_id=job_id,
            kind=EventKind.STATUS,
            message="JOB_STOPPED",
            payload_json={"reason": reason},
        )

    return {
        "finalized_failed": int(res_running.rowcount or 0) if res_running is not None else 0,
        "finalized_stopped": int(res_stop_requested.rowcount or 0) if res_stop_requested is not None else 0,
    }


async def claim_next_pending_job(
    session: AsyncSession,
    *,
    job_type: JobType | None = None,
) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.PENDING)
        .order_by(Job.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job_type is not None:
        stmt = stmt.where(Job.type == job_type)
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        return None
    job.status = JobStatus.RUNNING
    job.started_at = datetime.now()
    job.updated_at = datetime.now()
    await session.flush()
    return job


async def set_job_finished(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    status: JobStatus,
    result_json: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    await session.execute(
        update(Job)
        .where(Job.job_id == job_id)
        .values(
            status=status,
            result_json=result_json,
            error=error,
            ended_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )


async def request_stop(session: AsyncSession, job_id: uuid.UUID) -> JobStatus | None:
    """Request a job stop.

    Semantics:
    - PENDING and never started => STOPPED immediately (acts like cancel)
    - RUNNING => STOP_REQUESTED (runner will stop gracefully)
    """
    now = datetime.now()

    # Cancel queued job immediately.
    res_queued = await session.execute(
        update(Job)
        .where(Job.job_id == job_id)
        .where(Job.started_at.is_(None))
        .where(Job.status.in_([JobStatus.PENDING, JobStatus.STOP_REQUESTED]))
        .values(status=JobStatus.STOPPED, ended_at=now, updated_at=now)
    )
    if res_queued.rowcount:
        return JobStatus.STOPPED

    # Request stop for running job.
    res_running = await session.execute(
        update(Job)
        .where(Job.job_id == job_id)
        .where(Job.started_at.is_not(None))
        .where(Job.status.in_([JobStatus.RUNNING, JobStatus.STOP_REQUESTED]))
        .values(status=JobStatus.STOP_REQUESTED, updated_at=now)
    )
    if res_running.rowcount:
        return JobStatus.STOP_REQUESTED

    return None


async def stop_all_jobs(session: AsyncSession, *, job_type: JobType | None = None) -> dict[str, int]:
    """Stop all active jobs.

    Semantics (MVP-safe):
    - RUNNING (or STOP_REQUESTED but started) => STOP_REQUESTED (runner will stop gracefully)
    - PENDING/STOP_REQUESTED but never started => STOPPED immediately (won't be picked up by runner)
    """
    now = datetime.now()

    queued_stmt = (
        select(Job.job_id)
        .where(Job.started_at.is_(None))
        .where(Job.status.in_([JobStatus.PENDING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        queued_stmt = queued_stmt.where(Job.type == job_type)
    queued_ids = list((await session.execute(queued_stmt)).scalars().all())

    running_stmt = (
        select(Job.job_id)
        .where(Job.started_at.is_not(None))
        .where(Job.status.in_([JobStatus.RUNNING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        running_stmt = running_stmt.where(Job.type == job_type)
    running_ids = list((await session.execute(running_stmt)).scalars().all())

    # Stop queued jobs immediately so they don't block new runs.
    queued_update = (
        update(Job)
        .where(Job.started_at.is_(None))
        .where(Job.status.in_([JobStatus.PENDING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        queued_update = queued_update.where(Job.type == job_type)
    res_queued = await session.execute(
        queued_update.values(status=JobStatus.STOPPED, ended_at=now, updated_at=now)
    )

    # Request stop for running jobs.
    running_update = (
        update(Job)
        .where(Job.started_at.is_not(None))
        .where(Job.status.in_([JobStatus.RUNNING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        running_update = running_update.where(Job.type == job_type)
    res_running = await session.execute(running_update.values(status=JobStatus.STOP_REQUESTED, updated_at=now))

    for job_id in queued_ids:
        await append_event(
            session,
            job_id=job_id,
            kind=EventKind.STATUS,
            message="JOB_STOPPED",
            payload_json={"reason": "stop_all"},
        )

    for job_id in running_ids:
        await append_event(
            session,
            job_id=job_id,
            kind=EventKind.STATUS,
            message="STOP_REQUESTED",
            payload_json={"reason": "stop_all"},
        )

    return {
        "stopped_queued": int(res_queued.rowcount or 0),
        "stop_requested_running": int(res_running.rowcount or 0),
    }


async def append_event(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    kind: EventKind,
    message: str,
    level: str = "INFO",
    payload_json: dict[str, Any] | None = None,
) -> JobEvent:
    ev = JobEvent(job_id=job_id, kind=kind, level=level, message=message, payload_json=payload_json)
    session.add(ev)
    await session.flush()
    return ev


async def list_events(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    after_event_id: int = 0,
    limit: int = 200,
) -> list[JobEvent]:
    stmt: Select[tuple[JobEvent]] = (
        select(JobEvent)
        .where(JobEvent.job_id == job_id)
        .where(JobEvent.event_id > after_event_id)
        .order_by(JobEvent.event_id.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def upsert_order(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    symbol: str,
    order_id: int,
    side: str,
    order_type: str,
    status: str,
    quantity: float | None = None,
    price: float | None = None,
    executed_qty: float | None = None,
    avg_price: float | None = None,
    raw_json: dict[str, Any] | None = None,
) -> None:
    stmt = (
        insert(Order)
        .values(
            job_id=job_id,
            symbol=symbol,
            order_id=order_id,
            side=side,
            order_type=order_type,
            status=status,
            quantity=quantity,
            price=price,
            executed_qty=executed_qty,
            avg_price=avg_price,
            raw_json=raw_json,
        )
        .on_conflict_do_update(
            index_elements=[Order.job_id, Order.order_id],
            set_={
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "status": status,
                "quantity": quantity,
                "price": price,
                "executed_qty": executed_qty,
                "avg_price": avg_price,
                "raw_json": raw_json,
                "ts": datetime.now(),
            },
        )
    )
    await session.execute(stmt)


async def insert_trade(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    symbol: str,
    trade_id: int,
    order_id: int | None,
    quantity: float | None,
    price: float | None,
    realized_pnl: float | None,
    commission: float | None,
    raw_json: dict[str, Any] | None = None,
) -> None:
    stmt = (
        insert(Trade)
        .values(
            job_id=job_id,
            symbol=symbol,
            trade_id=trade_id,
            order_id=order_id,
            quantity=quantity,
            price=price,
            realized_pnl=realized_pnl,
            commission=commission,
            raw_json=raw_json,
        )
        .on_conflict_do_nothing(index_elements=[Trade.job_id, Trade.trade_id])
    )
    await session.execute(stmt)


async def list_orders(session: AsyncSession, *, job_id: uuid.UUID, limit: int = 200) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.job_id == job_id).order_by(Order.ts.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def list_trades(session: AsyncSession, *, job_id: uuid.UUID, limit: int = 200) -> list[Trade]:
    result = await session.execute(
        select(Trade).where(Trade.job_id == job_id).order_by(Trade.ts.desc()).limit(limit)
    )
    return list(result.scalars().all())
