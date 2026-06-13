from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from control.enums import EventKind, JobStatus, JobType
from control.models import (
    AccountSnapshot,
    AllocationMode,
    BinanceApiCredential,
    BridgeTransfer,
    Job,
    JobEvent,
    Order,
    StrategyAllocation,
    StrategyMeta,
    StrategyChatSession,
    StrategyQualityLog,
    Trade,
    UsageRecord,
    UserProfile,
    WalletAccount,
    WalletAccountStatus,
    WalletRole,
    WalletTransfer,
    WalletTransferStatus,
)

ACTIVE_STATUSES = {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.STOP_REQUESTED}
FINISHED_STATUSES = {JobStatus.SUCCEEDED, JobStatus.STOPPED, JobStatus.FAILED}
ACTIVE_STATUS_VALUES = {str(s) for s in ACTIVE_STATUSES}


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------


async def get_user_profile(session: AsyncSession, *, user_id: str) -> UserProfile | None:
    result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    return result.scalar_one_or_none()


async def upsert_user_profile(
    session: AsyncSession,
    *,
    user_id: str,
    email: str = "",
    display_name: str = "",
) -> UserProfile:
    now = datetime.now()
    stmt = (
        insert(UserProfile)
        .values(user_id=user_id, email=email, display_name=display_name, created_at=now, updated_at=now)
        .on_conflict_do_update(
            index_elements=[UserProfile.user_id],
            set_={"email": email, "updated_at": now},
        )
    )
    await session.execute(stmt)
    await session.flush()
    result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    return result.scalar_one()


async def update_user_plan(
    session: AsyncSession,
    *,
    user_id: str,
    plan: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    plan_expires_at: datetime | None = None,
) -> None:
    values: dict[str, Any] = {"plan": plan, "updated_at": datetime.now()}
    if stripe_customer_id is not None:
        values["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id is not None:
        values["stripe_subscription_id"] = stripe_subscription_id
    if plan_expires_at is not None:
        values["plan_expires_at"] = plan_expires_at
    await session.execute(update(UserProfile).where(UserProfile.user_id == user_id).values(**values))


# ---------------------------------------------------------------------------
# BinanceApiCredential
# ---------------------------------------------------------------------------

_VALID_ENVS = frozenset({"mainnet", "testnet"})


async def get_binance_credential(
    session: AsyncSession, *, user_id: str, env: str
) -> BinanceApiCredential | None:
    result = await session.execute(
        select(BinanceApiCredential).where(
            BinanceApiCredential.user_id == user_id,
            BinanceApiCredential.env == env,
        )
    )
    return result.scalar_one_or_none()


async def list_binance_credentials(
    session: AsyncSession, *, user_id: str
) -> list[BinanceApiCredential]:
    result = await session.execute(
        select(BinanceApiCredential).where(BinanceApiCredential.user_id == user_id)
    )
    return list(result.scalars().all())




async def upsert_binance_credential(
    session: AsyncSession,
    *,
    user_id: str,
    env: str,
    api_key_enc: str,
    api_secret_enc: str,
    ip_whitelist: list[str] | None = None,
) -> None:
    now = datetime.now()
    values: dict[str, Any] = {
        "user_id": user_id,
        "env": env,
        "api_key_enc": api_key_enc,
        "api_secret_enc": api_secret_enc,
        "created_at": now,
        "updated_at": now,
    }
    update_set: dict[str, Any] = {
        "api_key_enc": api_key_enc,
        "api_secret_enc": api_secret_enc,
        "updated_at": now,
    }
    if ip_whitelist is not None:
        values["ip_whitelist"] = list(ip_whitelist)
        update_set["ip_whitelist"] = list(ip_whitelist)
    stmt = (
        insert(BinanceApiCredential)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_binance_cred_user_env",
            set_=update_set,
        )
    )
    await session.execute(stmt)


async def delete_binance_credential(
    session: AsyncSession, *, user_id: str, env: str
) -> None:
    await session.execute(
        delete(BinanceApiCredential).where(
            BinanceApiCredential.user_id == user_id,
            BinanceApiCredential.env == env,
        )
    )


async def get_user_by_stripe_customer_id(
    session: AsyncSession, *, stripe_customer_id: str
) -> UserProfile | None:
    result = await session.execute(
        select(UserProfile).where(UserProfile.stripe_customer_id == stripe_customer_id)
    )
    return result.scalar_one_or_none()


async def update_user_auto_sweep_settings(
    session: AsyncSession,
    *,
    user_id: str,
    enabled: bool,
    futures_buffer_usdt: float,
    sweep_threshold_usdt: float,
    margin_restore_usdt: float,
) -> None:
    await session.execute(
        update(UserProfile)
        .where(UserProfile.user_id == user_id)
        .values(
            auto_sweep_enabled=enabled,
            auto_sweep_futures_buffer_usdt=futures_buffer_usdt,
            auto_sweep_sweep_threshold_usdt=sweep_threshold_usdt,
            auto_sweep_margin_restore_usdt=margin_restore_usdt,
            updated_at=datetime.now(),
        )
    )


async def list_auto_sweep_enabled_users(session: AsyncSession) -> list[UserProfile]:
    result = await session.execute(
        select(UserProfile).where(UserProfile.auto_sweep_enabled.is_(True))
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# StrategyMeta
# ---------------------------------------------------------------------------


async def get_strategy_meta_by_name(
    session: AsyncSession,
    *,
    user_id: str,
    strategy_name: str,
) -> StrategyMeta | None:
    result = await session.execute(
        select(StrategyMeta)
        .where(StrategyMeta.user_id == user_id)
        .where(StrategyMeta.strategy_name == strategy_name)
        .order_by(StrategyMeta.updated_at.desc(), StrategyMeta.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_strategy_meta(
    session: AsyncSession,
    *,
    user_id: str,
) -> list[StrategyMeta]:
    result = await session.execute(
        select(StrategyMeta)
        .where(StrategyMeta.user_id == user_id)
        .order_by(StrategyMeta.updated_at.desc(), StrategyMeta.id.desc())
    )
    return list(result.scalars().all())


async def upsert_strategy_meta(
    session: AsyncSession,
    *,
    user_id: str,
    strategy_name: str,
    blob_path: str,
    summary: str | None = None,
) -> StrategyMeta:
    existing = await get_strategy_meta_by_name(session, user_id=user_id, strategy_name=strategy_name)
    now = datetime.now()
    if existing is None:
        existing = StrategyMeta(
            user_id=user_id,
            strategy_name=strategy_name,
            blob_path=blob_path,
            summary=summary,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
        await session.flush()
        return existing

    existing.blob_path = blob_path
    existing.summary = summary
    existing.updated_at = now
    await session.flush()
    return existing


async def delete_strategy_meta_by_name(
    session: AsyncSession,
    *,
    user_id: str,
    strategy_name: str,
) -> bool:
    res = await session.execute(
        delete(StrategyMeta)
        .where(StrategyMeta.user_id == user_id)
        .where(StrategyMeta.strategy_name == strategy_name)
    )
    return bool(res.rowcount)


# ---------------------------------------------------------------------------
# UsageRecord
# ---------------------------------------------------------------------------


async def increment_usage(
    session: AsyncSession,
    *,
    user_id: str,
    action: str,
    period_key: str,
) -> int:
    stmt = (
        insert(UsageRecord)
        .values(user_id=user_id, action=action, period_key=period_key, count=1)
        .on_conflict_do_update(
            constraint="uq_usage_user_action_period",
            set_={"count": UsageRecord.count + 1, "ts": datetime.now()},
        )
        .returning(UsageRecord.count)
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_usage_count(
    session: AsyncSession,
    *,
    user_id: str,
    action: str,
    period_key: str,
) -> int:
    result = await session.execute(
        select(UsageRecord.count)
        .where(UsageRecord.user_id == user_id)
        .where(UsageRecord.action == action)
        .where(UsageRecord.period_key == period_key)
    )
    return int(result.scalar_one_or_none() or 0)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


async def create_job(
    session: AsyncSession,
    *,
    user_id: str,
    job_type: JobType,
    strategy_path: str,
    config_json: dict[str, Any],
    wallet_account_id: uuid.UUID | None = None,
) -> Job:
    job = Job(
        user_id=user_id,
        type=job_type,
        status=JobStatus.PENDING,
        strategy_path=strategy_path,
        config_json=config_json,
        wallet_account_id=wallet_account_id,
    )
    session.add(job)
    await session.flush()
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID, *, user_id: str | None = None) -> Job | None:
    stmt = select(Job).where(Job.job_id == job_id)
    if user_id is not None:
        stmt = stmt.where(Job.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_jobs(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 50,
    job_type: JobType | None = None,
    status: JobStatus | None = None,
) -> list[Job]:
    stmt: Select[tuple[Job]] = select(Job).where(Job.user_id == user_id)
    if job_type is not None:
        stmt = stmt.where(Job.type == job_type)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    result = await session.execute(stmt.order_by(Job.created_at.desc()).limit(limit))
    return list(result.scalars().all())


# Heavy keys stripped from result_json at SQL projection time for list endpoints.
# Keep in sync with src/api/main.py JobSummary serializer expectations.
_HEAVY_RESULT_KEYS: tuple[str, ...] = ("chart", "trades")


async def list_job_summaries(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 50,
    job_type: JobType | None = None,
    status: JobStatus | None = None,
) -> list[Row[Any]]:
    """List jobs with a slimmed-down ``result_summary`` projection.

    Strips heavy keys (``chart``, ``trades``) from ``result_json`` at the SQL
    layer so the API process never materializes multi-MB JSONB blobs in memory.
    Use this for list/summary endpoints; for full result payloads keep using
    :func:`get_job` / :func:`list_jobs`.
    """
    slim_expr = Job.result_json
    for key in _HEAVY_RESULT_KEYS:
        slim_expr = slim_expr.op("-")(key)
    result_summary = case(
        (
            func.jsonb_typeof(Job.result_json) == "object",
            slim_expr,
        ),
        else_=Job.result_json,
    ).label("result_summary")

    stmt = select(
        Job.job_id,
        Job.type,
        Job.status,
        Job.strategy_path,
        Job.wallet_account_id,
        Job.config_json,
        Job.error,
        Job.created_at,
        Job.started_at,
        Job.ended_at,
        result_summary,
    ).where(Job.user_id == user_id)
    if job_type is not None:
        stmt = stmt.where(Job.type == job_type)
    if status is not None:
        stmt = stmt.where(Job.status == status)

    result = await session.execute(stmt.order_by(Job.created_at.desc()).limit(limit))
    return list(result.all())


def _slim_result_summary_column() -> Any:
    """JSONB projection that strips heavy result keys (chart, trades).

    Shared by job summary and sweep queries so the API process never loads
    multi-MB result blobs when it only needs aggregate metrics.
    """
    slim_expr = Job.result_json
    for key in _HEAVY_RESULT_KEYS:
        slim_expr = slim_expr.op("-")(key)
    return case(
        (func.jsonb_typeof(Job.result_json) == "object", slim_expr),
        else_=Job.result_json,
    ).label("result_summary")


async def list_sweep_child_rows(
    session: AsyncSession,
    *,
    user_id: str,
    sweep_id: str,
) -> list[Row[Any]]:
    """Child BACKTEST jobs belonging to ``sweep_id`` with slim result summaries."""
    result_summary = _slim_result_summary_column()
    stmt = (
        select(
            Job.job_id,
            Job.status,
            Job.strategy_path,
            Job.config_json,
            Job.error,
            Job.created_at,
            Job.started_at,
            Job.ended_at,
            result_summary,
        )
        .where(Job.user_id == user_id)
        .where(Job.type == JobType.BACKTEST)
        .where(Job.config_json["_sweep"]["sweep_id"].astext == sweep_id)
    )
    result = await session.execute(stmt)
    return list(result.all())


async def list_sweep_group_rows(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 500,
) -> list[Row[Any]]:
    """Recent BACKTEST jobs that belong to any sweep (have ``config_json._sweep``)."""
    result_summary = _slim_result_summary_column()
    stmt = (
        select(
            Job.job_id,
            Job.status,
            Job.strategy_path,
            Job.config_json,
            Job.error,
            Job.created_at,
            Job.started_at,
            Job.ended_at,
            result_summary,
        )
        .where(Job.user_id == user_id)
        .where(Job.type == JobType.BACKTEST)
        .where(Job.config_json.has_key("_sweep"))  # noqa: W601 - JSONB ? operator
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.all())


async def count_jobs(
    session: AsyncSession,
    *,
    user_id: str,
    job_type: JobType | None = None,
) -> int:
    stmt = select(func.count()).select_from(Job).where(Job.user_id == user_id)
    if job_type is not None:
        stmt = stmt.where(Job.type == job_type)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def count_active_jobs(
    session: AsyncSession,
    *,
    user_id: str,
    job_type: JobType | None = None,
) -> int:
    stmt = select(func.count()).select_from(Job).where(Job.user_id == user_id).where(Job.status.in_(ACTIVE_STATUSES))
    if job_type is not None:
        stmt = stmt.where(Job.type == job_type)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def delete_job(
    session: AsyncSession, job_id: uuid.UUID, *, user_id: str
) -> tuple[bool, JobStatus | None]:
    job = await get_job(session, job_id, user_id=user_id)
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
    user_id: str,
    job_type: JobType | None = None,
) -> dict[str, int]:
    base = select(func.count()).select_from(Job).where(Job.user_id == user_id)
    active_stmt = base.where(Job.status.in_(ACTIVE_STATUSES))
    if job_type is not None:
        active_stmt = active_stmt.where(Job.type == job_type)
    active_count = int((await session.execute(active_stmt)).scalar_one() or 0)

    delete_stmt = delete(Job).where(Job.user_id == user_id).where(Job.status.in_(FINISHED_STATUSES))
    if job_type is not None:
        delete_stmt = delete_stmt.where(Job.type == job_type)
    res = await session.execute(delete_stmt)
    return {
        "deleted": int(res.rowcount or 0),
        "skipped_active": active_count,
    }


async def finalize_orphaned_jobs(
    session: AsyncSession,
    *,
    reason: str = "runner_restart",
    job_type_filter: JobType | None = None,
) -> dict[str, int]:
    """Reconcile RUNNING/STOP_REQUESTED jobs from a previous runner.

    When ``job_type_filter`` is provided, only jobs of that type are
    reconciled. Used by the split-role runners (RUNNER_ROLE=live or
    =backtest) so each container only touches its own job type and does
    not interfere with the other container's in-flight jobs.
    """
    now = datetime.now()

    running_stmt = (
        select(Job.job_id, Job.type)
        .where(Job.ended_at.is_(None))
        .where(Job.status == JobStatus.RUNNING)
    )
    if job_type_filter is not None:
        running_stmt = running_stmt.where(Job.type == job_type_filter)
    running_rows = list((await session.execute(running_stmt)).all())
    running_live_ids = [job_id for job_id, job_type in running_rows if str(job_type) == str(JobType.LIVE)]
    running_backtest_ids = [job_id for job_id, job_type in running_rows if str(job_type) != str(JobType.LIVE)]

    stop_requested_stmt = (
        select(Job.job_id)
        .where(Job.ended_at.is_(None))
        .where(Job.status == JobStatus.STOP_REQUESTED)
    )
    if job_type_filter is not None:
        stop_requested_stmt = stop_requested_stmt.where(Job.type == job_type_filter)
    stop_requested_ids = list((await session.execute(stop_requested_stmt)).scalars().all())

    # Requeue orphaned backtest jobs (PENDING) so they auto-retry on the next runner.
    res_backtest_requeued = None
    if running_backtest_ids:
        res_backtest_requeued = await session.execute(
            update(Job)
            .where(Job.job_id.in_(running_backtest_ids))
            .values(
                status=JobStatus.PENDING,
                started_at=None,
                ended_at=None,
                updated_at=now,
                error=None,
                result_json=None,
            )
        )

    res_live_requeued = None
    if running_live_ids:
        res_live_requeued = await session.execute(
            update(Job)
            .where(Job.job_id.in_(running_live_ids))
            .values(
                status=JobStatus.PENDING,
                started_at=None,
                ended_at=None,
                updated_at=now,
                error=None,
                live_heartbeat_at=None,
            )
        )

    res_stop_requested = None
    if stop_requested_ids:
        res_stop_requested = await session.execute(
            update(Job)
            .where(Job.job_id.in_(stop_requested_ids))
            .values(status=JobStatus.STOPPED, ended_at=now, updated_at=now)
        )

    for jid in running_backtest_ids:
        await append_event(session, job_id=jid, kind=EventKind.STATUS, message="JOB_REQUEUED",
                           payload_json={"reason": reason, "resume": False})
    for jid in running_live_ids:
        await append_event(session, job_id=jid, kind=EventKind.STATUS, message="JOB_REQUEUED",
                           payload_json={"reason": reason, "resume": True})
    for jid in stop_requested_ids:
        await append_event(session, job_id=jid, kind=EventKind.STATUS, message="JOB_STOPPED",
                           payload_json={"reason": reason})

    return {
        "requeued_backtest": int(res_backtest_requeued.rowcount or 0) if res_backtest_requeued is not None else 0,
        "requeued_live": int(res_live_requeued.rowcount or 0) if res_live_requeued is not None else 0,
        "finalized_stopped": int(res_stop_requested.rowcount or 0) if res_stop_requested is not None else 0,
    }


async def find_stale_live_job_ids(
    session: AsyncSession,
    *,
    stale_seconds: int,
    initial_grace_seconds: int,
) -> list[uuid.UUID]:
    """Return job_ids of LIVE+RUNNING jobs whose DB heartbeat is older than
    ``stale_seconds`` (or absent past the initial grace window).

    The caller is expected to additionally consult an out-of-band liveness
    signal (e.g. Redis) before deciding to requeue, so that DB pool churn on
    the API layer cannot falsely mark a healthy runner as stale.
    """
    now = datetime.now()
    stale_cutoff = now - timedelta(seconds=max(30, stale_seconds))
    grace_cutoff = now - timedelta(seconds=max(60, initial_grace_seconds))

    stale_cond = or_(
        and_(Job.live_heartbeat_at.is_(None), Job.started_at < grace_cutoff),
        Job.live_heartbeat_at < stale_cutoff,
    )
    rows = (
        await session.execute(
            select(Job.job_id).where(
                Job.type == JobType.LIVE,
                Job.status == JobStatus.RUNNING,
                Job.ended_at.is_(None),
                Job.started_at.is_not(None),
                stale_cond,
            )
        )
    ).scalars().all()
    return list(rows)


async def requeue_live_jobs_by_ids(
    session: AsyncSession,
    job_ids: list[uuid.UUID],
    *,
    reason: str,
) -> int:
    """Requeue the given LIVE job_ids back to PENDING and emit JOB_REQUEUED."""
    if not job_ids:
        return 0
    now = datetime.now()
    await session.execute(
        update(Job)
        .where(Job.job_id.in_(job_ids))
        .values(
            status=JobStatus.PENDING,
            started_at=None,
            ended_at=None,
            updated_at=now,
            error=None,
            live_heartbeat_at=None,
        )
    )
    for jid in job_ids:
        await append_event(
            session,
            job_id=jid,
            kind=EventKind.STATUS,
            message="JOB_REQUEUED",
            payload_json={"reason": reason, "resume": True},
        )
    return len(job_ids)


async def requeue_stale_live_jobs(
    session: AsyncSession,
    *,
    stale_seconds: int,
    initial_grace_seconds: int,
    reason: str = "stale_live_heartbeat",
) -> int:
    """LIVE + RUNNING 잡 중 하트비트가 오래된 것을 PENDING으로 되돌린다 (프로세스 고아 복구).

    Note: This helper consults the DB heartbeat column only. Callers that
    additionally maintain a Redis liveness key should prefer the two-step
    ``find_stale_live_job_ids`` + ``requeue_live_jobs_by_ids`` pattern so
    they can skip jobs whose Redis heartbeat is still fresh.
    """
    stale_rows = await find_stale_live_job_ids(
        session,
        stale_seconds=stale_seconds,
        initial_grace_seconds=initial_grace_seconds,
    )
    return await requeue_live_jobs_by_ids(session, stale_rows, reason=reason)


async def update_live_job_heartbeat(session: AsyncSession, job_id: uuid.UUID) -> None:
    now = datetime.now()
    await session.execute(
        update(Job)
        .where(Job.job_id == job_id)
        .where(Job.status == JobStatus.RUNNING)
        .values(live_heartbeat_at=now, updated_at=now)
    )


async def store_live_initial_equity(
    session: AsyncSession, job_id: uuid.UUID, initial_equity: float
) -> None:
    """Store initial_equity in result_json while the live job is still running."""
    await session.execute(
        update(Job)
        .where(Job.job_id == job_id)
        .where(Job.status == JobStatus.RUNNING)
        .values(
            result_json={"summary": {"initial_equity": initial_equity}},
            updated_at=datetime.now(),
        )
    )


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
    job.live_heartbeat_at = None
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
            live_heartbeat_at=None,
        )
    )


async def request_stop(session: AsyncSession, job_id: uuid.UUID, *, user_id: str) -> JobStatus | None:
    now = datetime.now()

    res_queued = await session.execute(
        update(Job)
        .where(Job.job_id == job_id)
        .where(Job.user_id == user_id)
        .where(Job.started_at.is_(None))
        .where(Job.status.in_([JobStatus.PENDING, JobStatus.STOP_REQUESTED]))
        .values(status=JobStatus.STOPPED, ended_at=now, updated_at=now)
    )
    if res_queued.rowcount:
        return JobStatus.STOPPED

    res_running = await session.execute(
        update(Job)
        .where(Job.job_id == job_id)
        .where(Job.user_id == user_id)
        .where(Job.started_at.is_not(None))
        .where(Job.status.in_([JobStatus.RUNNING, JobStatus.STOP_REQUESTED]))
        .values(status=JobStatus.STOP_REQUESTED, updated_at=now)
    )
    if res_running.rowcount:
        return JobStatus.STOP_REQUESTED

    return None


async def stop_all_jobs(
    session: AsyncSession, *, user_id: str, job_type: JobType | None = None
) -> dict[str, int]:
    now = datetime.now()

    queued_stmt = (
        select(Job.job_id)
        .where(Job.user_id == user_id)
        .where(Job.started_at.is_(None))
        .where(Job.status.in_([JobStatus.PENDING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        queued_stmt = queued_stmt.where(Job.type == job_type)
    queued_ids = list((await session.execute(queued_stmt)).scalars().all())

    running_stmt = (
        select(Job.job_id)
        .where(Job.user_id == user_id)
        .where(Job.started_at.is_not(None))
        .where(Job.status.in_([JobStatus.RUNNING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        running_stmt = running_stmt.where(Job.type == job_type)
    running_ids = list((await session.execute(running_stmt)).scalars().all())

    queued_update = (
        update(Job)
        .where(Job.user_id == user_id)
        .where(Job.started_at.is_(None))
        .where(Job.status.in_([JobStatus.PENDING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        queued_update = queued_update.where(Job.type == job_type)
    res_queued = await session.execute(queued_update.values(status=JobStatus.STOPPED, ended_at=now, updated_at=now))

    running_update = (
        update(Job)
        .where(Job.user_id == user_id)
        .where(Job.started_at.is_not(None))
        .where(Job.status.in_([JobStatus.RUNNING, JobStatus.STOP_REQUESTED]))
    )
    if job_type is not None:
        running_update = running_update.where(Job.type == job_type)
    res_running = await session.execute(running_update.values(status=JobStatus.STOP_REQUESTED, updated_at=now))

    for jid in queued_ids:
        await append_event(session, job_id=jid, kind=EventKind.STATUS, message="JOB_STOPPED",
                           payload_json={"reason": "stop_all"})
    for jid in running_ids:
        await append_event(session, job_id=jid, kind=EventKind.STATUS, message="STOP_REQUESTED",
                           payload_json={"reason": "stop_all"})

    return {
        "stopped_queued": int(res_queued.rowcount or 0),
        "stop_requested_running": int(res_running.rowcount or 0),
    }


# ---------------------------------------------------------------------------
# Events / Orders / Trades
# ---------------------------------------------------------------------------


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
            job_id=job_id, symbol=symbol, order_id=order_id, side=side,
            order_type=order_type, status=status, quantity=quantity,
            price=price, executed_qty=executed_qty, avg_price=avg_price,
            raw_json=raw_json,
        )
        .on_conflict_do_update(
            index_elements=[Order.job_id, Order.order_id],
            set_={
                "symbol": symbol, "side": side, "order_type": order_type,
                "status": status, "quantity": quantity, "price": price,
                "executed_qty": executed_qty, "avg_price": avg_price,
                "raw_json": raw_json, "ts": datetime.now(),
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
    has_reason = isinstance(raw_json, dict) and bool(
        raw_json.get("reason") or raw_json.get("exit_reason")
    )
    base = insert(Trade).values(
        job_id=job_id, symbol=symbol, trade_id=trade_id, order_id=order_id,
        quantity=quantity, price=price, realized_pnl=realized_pnl,
        commission=commission, raw_json=raw_json,
    )
    if has_reason:
        # reason을 가진 체결이 나중에 도착하면, reason 없이 먼저 적재된
        # 동일 체결 행의 raw_json을 보정한다(Maker 비동기 체결 레이스 대비).
        stmt = base.on_conflict_do_update(
            index_elements=[Trade.job_id, Trade.trade_id],
            set_={"raw_json": base.excluded.raw_json},
            where=or_(
                Trade.raw_json.is_(None),
                ~Trade.raw_json.has_key("reason"),
            ),
        )
    else:
        stmt = base.on_conflict_do_nothing(
            index_elements=[Trade.job_id, Trade.trade_id]
        )
    await session.execute(stmt)


async def list_orders(session: AsyncSession, *, job_id: uuid.UUID, limit: int = 200) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.job_id == job_id).order_by(Order.ts.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def list_trade_ids(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> set[int]:
    """Fetch all trade_ids for a job (for backfill dedup)."""
    result = await session.execute(
        select(Trade.trade_id).where(Trade.job_id == job_id)
    )
    return set(result.scalars().all())


async def list_trades(session: AsyncSession, *, job_id: uuid.UUID, limit: int = 200) -> list[Trade]:
    result = await session.execute(
        select(Trade).where(Trade.job_id == job_id).order_by(Trade.ts.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def list_trades_batch(
    session: AsyncSession,
    *,
    job_ids: list[uuid.UUID],
    limit_per_job: int = 200,
) -> dict[uuid.UUID, list[Trade]]:
    """Fetch trades for multiple jobs in a single query."""
    if not job_ids:
        return {}
    from sqlalchemy import func as sa_func  # noqa: F811
    # Use window function to rank per job, then filter
    row_num = sa_func.row_number().over(
        partition_by=Trade.job_id,
        order_by=Trade.ts.desc(),
    ).label("rn")
    subq = (
        select(Trade, row_num)
        .where(Trade.job_id.in_(job_ids))
        .subquery()
    )
    result = await session.execute(
        select(Trade).join(subq, Trade.id == subq.c.id).where(subq.c.rn <= limit_per_job)
    )
    rows = list(result.scalars().all())
    out: dict[uuid.UUID, list[Trade]] = {jid: [] for jid in job_ids}
    for trade in rows:
        out.setdefault(trade.job_id, []).append(trade)
    return out


# ---------------------------------------------------------------------------
# Strategy quality logs
# ---------------------------------------------------------------------------


async def create_strategy_quality_log(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    pipeline_version: str,
    endpoint: str,
    user_prompt_len: int,
    message_count: int,
    intent: str | None,
    status: str | None,
    missing_fields: list[str],
    unsupported_requirements: list[str],
    development_requirements: list[str],
    generation_attempted: bool | None,
    generation_success: bool | None,
    verification_passed: bool | None,
    repaired: bool | None,
    repair_attempts: int,
    model_used: str | None,
    error_stage: str | None,
    error_message: str | None,
    duration_ms: int,
    meta_json: dict[str, Any] | None = None,
) -> StrategyQualityLog:
    row = StrategyQualityLog(
        request_id=request_id, pipeline_version=pipeline_version, endpoint=endpoint,
        user_prompt_len=max(0, int(user_prompt_len)), message_count=max(0, int(message_count)),
        intent=intent, status=status, missing_fields=missing_fields,
        unsupported_requirements=unsupported_requirements,
        development_requirements=development_requirements,
        generation_attempted=generation_attempted, generation_success=generation_success,
        verification_passed=verification_passed, repaired=repaired,
        repair_attempts=max(0, int(repair_attempts)), model_used=model_used,
        error_stage=error_stage, error_message=error_message,
        duration_ms=max(0, int(duration_ms)), meta_json=meta_json,
    )
    session.add(row)
    await session.flush()
    return row


async def list_strategy_quality_logs(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    limit: int = 5000,
) -> list[StrategyQualityLog]:
    stmt: Select[tuple[StrategyQualityLog]] = select(StrategyQualityLog).order_by(StrategyQualityLog.ts.desc())
    if since is not None:
        stmt = stmt.where(StrategyQualityLog.ts >= since)
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Strategy chat sessions
# ---------------------------------------------------------------------------


async def list_strategy_chat_sessions(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 200,
) -> list[StrategyChatSession]:
    result = await session.execute(
        select(StrategyChatSession)
        .where(StrategyChatSession.user_id == user_id)
        .order_by(StrategyChatSession.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_strategy_chat_session_summaries(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 200,
) -> list[StrategyChatSession]:
    """Load sessions with deferred data_json to reduce payload."""
    result = await session.execute(
        select(StrategyChatSession)
        .where(StrategyChatSession.user_id == user_id)
        .order_by(StrategyChatSession.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_strategy_chat_session(
    session: AsyncSession,
    *,
    user_id: str,
    session_id: str,
) -> StrategyChatSession | None:
    result = await session.execute(
        select(StrategyChatSession)
        .where(StrategyChatSession.user_id == user_id)
        .where(StrategyChatSession.session_id == session_id)
    )
    return result.scalar_one_or_none()


async def upsert_strategy_chat_session(
    session: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    title: str,
    data_json: dict[str, Any],
) -> StrategyChatSession:
    now = datetime.now()
    stmt = (
        insert(StrategyChatSession)
        .values(user_id=user_id, session_id=session_id, title=title,
                data_json=data_json, created_at=now, updated_at=now)
        .on_conflict_do_update(
            index_elements=[StrategyChatSession.user_id, StrategyChatSession.session_id],
            set_={"title": title, "data_json": data_json, "updated_at": now},
        )
        .returning(StrategyChatSession)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    refreshed = await session.execute(
        select(StrategyChatSession)
        .where(StrategyChatSession.user_id == user_id)
        .where(StrategyChatSession.session_id == session_id)
    )
    saved = refreshed.scalar_one_or_none()
    if saved is None:
        raise RuntimeError("Failed to upsert strategy chat session")
    return saved


async def delete_strategy_chat_session(
    session: AsyncSession,
    *,
    user_id: str,
    session_id: str,
) -> bool:
    res = await session.execute(
        delete(StrategyChatSession)
        .where(StrategyChatSession.user_id == user_id)
        .where(StrategyChatSession.session_id == session_id)
    )
    return bool(res.rowcount)


# ---------------------------------------------------------------------------
# Account snapshots
# ---------------------------------------------------------------------------


async def upsert_account_snapshot(
    session: AsyncSession,
    *,
    key: str,
    data_json: dict[str, Any],
) -> None:
    stmt = (
        insert(AccountSnapshot)
        .values(key=key, data_json=data_json, updated_at=datetime.now())
        .on_conflict_do_update(
            index_elements=[AccountSnapshot.key],
            set_={"data_json": data_json, "updated_at": datetime.now()},
        )
    )
    await session.execute(stmt)


async def get_account_snapshot(
    session: AsyncSession,
    *,
    key: str,
) -> AccountSnapshot | None:
    result = await session.execute(
        select(AccountSnapshot).where(AccountSnapshot.key == key)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Upbit keys
# ---------------------------------------------------------------------------


async def update_user_upbit_keys(
    session: AsyncSession,
    *,
    user_id: str,
    api_key_enc: str | None,
    api_secret_enc: str | None,
) -> None:
    await session.execute(
        update(UserProfile)
        .where(UserProfile.user_id == user_id)
        .values(
            upbit_api_key_enc=api_key_enc,
            upbit_api_secret_enc=api_secret_enc,
            updated_at=datetime.now(),
        )
    )


# ---------------------------------------------------------------------------
# Bridge transfers
# ---------------------------------------------------------------------------


async def create_bridge_transfer(
    session: AsyncSession,
    *,
    user_id: str,
    direction: str,
    network: str,
    requested_usdt: float,
    dst_deposit_address: str | None = None,
    krw_amount: float | None = None,
) -> BridgeTransfer:
    record = BridgeTransfer(
        id=uuid.uuid4(),
        user_id=user_id,
        direction=direction,
        status="PENDING",
        network=network,
        requested_usdt=requested_usdt,
        dst_deposit_address=dst_deposit_address,
        krw_amount=krw_amount,
    )
    session.add(record)
    await session.flush()
    return record


async def get_bridge_transfer(
    session: AsyncSession,
    *,
    transfer_id: uuid.UUID,
    user_id: str,
) -> BridgeTransfer | None:
    result = await session.execute(
        select(BridgeTransfer).where(
            BridgeTransfer.id == transfer_id,
            BridgeTransfer.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_bridge_transfers(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 50,
) -> list[BridgeTransfer]:
    result = await session.execute(
        select(BridgeTransfer)
        .where(BridgeTransfer.user_id == user_id)
        .order_by(BridgeTransfer.initiated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_bridge_transfer(
    session: AsyncSession,
    *,
    transfer_id: uuid.UUID,
    **kwargs: Any,
) -> None:
    kwargs["updated_at"] = datetime.now()
    await session.execute(
        update(BridgeTransfer)
        .where(BridgeTransfer.id == transfer_id)
        .values(**kwargs)
    )


# ---------------------------------------------------------------------------
# WalletAccount (Sub-account topology)
# ---------------------------------------------------------------------------


_VALID_WALLET_ROLES = frozenset({WalletRole.MASTER, WalletRole.SUB})


def _wallet_status_value(status: WalletAccountStatus | str) -> str:
    return status.value if isinstance(status, WalletAccountStatus) else str(status)


async def get_wallet_account(
    session: AsyncSession, *, wallet_account_id: uuid.UUID
) -> WalletAccount | None:
    result = await session.execute(
        select(WalletAccount).where(WalletAccount.id == wallet_account_id)
    )
    return result.scalar_one_or_none()


async def get_wallet_account_by_alias(
    session: AsyncSession, *, user_id: str, env: str, alias: str
) -> WalletAccount | None:
    result = await session.execute(
        select(WalletAccount).where(
            WalletAccount.user_id == user_id,
            WalletAccount.env == env,
            WalletAccount.alias == alias,
        )
    )
    return result.scalar_one_or_none()


async def get_master_wallet_account(
    session: AsyncSession, *, user_id: str, env: str
) -> WalletAccount | None:
    """Return the user's master wallet for the given env, if any.

    Only one master per (user_id, env) is expected; if duplicates exist for
    any reason (e.g. partial migration), the most recently updated wins.
    """
    result = await session.execute(
        select(WalletAccount)
        .where(
            WalletAccount.user_id == user_id,
            WalletAccount.env == env,
            WalletAccount.role == WalletRole.MASTER,
        )
        .order_by(WalletAccount.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_wallet_accounts(
    session: AsyncSession,
    *,
    user_id: str,
    env: str | None = None,
    role: WalletRole | str | None = None,
) -> list[WalletAccount]:
    stmt = select(WalletAccount).where(WalletAccount.user_id == user_id)
    if env is not None:
        stmt = stmt.where(WalletAccount.env == env)
    if role is not None:
        role_value = role.value if isinstance(role, WalletRole) else role
        stmt = stmt.where(WalletAccount.role == role_value)
    stmt = stmt.order_by(WalletAccount.role.desc(), WalletAccount.alias.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_wallet_account(  # noqa: PLR0913 — each kwarg maps to a distinct table column
    session: AsyncSession,
    *,
    user_id: str,
    env: str,
    role: WalletRole | str,
    alias: str,
    purpose: str = "generic",
    sub_account_email: str | None = None,
    api_key_enc: str | None = None,
    api_secret_enc: str | None = None,
    enabled_wallets: dict[str, Any] | None = None,
    ip_whitelist: list[str] | None = None,
    status: WalletAccountStatus | str = WalletAccountStatus.KEY_MISSING,
) -> WalletAccount:
    role_value = role.value if isinstance(role, WalletRole) else role
    if role_value not in {r.value for r in _VALID_WALLET_ROLES}:
        raise ValueError(f"invalid wallet role: {role_value!r}")
    if role_value == WalletRole.SUB and not sub_account_email:
        raise ValueError("sub_account_email is required when role='sub'")

    wallet = WalletAccount(
        user_id=user_id,
        env=env,
        role=role_value,
        sub_account_email=sub_account_email,
        alias=alias,
        purpose=purpose,
        api_key_enc=api_key_enc,
        api_secret_enc=api_secret_enc,
        enabled_wallets=enabled_wallets or {},
        ip_whitelist=ip_whitelist or [],
        status=_wallet_status_value(status),
    )
    session.add(wallet)
    await session.flush()
    return wallet


async def update_wallet_account_keys(
    session: AsyncSession,
    *,
    wallet_account_id: uuid.UUID,
    api_key_enc: str,
    api_secret_enc: str,
    mark_active: bool = True,
) -> None:
    values: dict[str, Any] = {
        "api_key_enc": api_key_enc,
        "api_secret_enc": api_secret_enc,
        "updated_at": datetime.now(),
    }
    if mark_active:
        values["status"] = WalletAccountStatus.ACTIVE.value
    await session.execute(
        update(WalletAccount)
        .where(WalletAccount.id == wallet_account_id)
        .values(**values)
    )


async def update_wallet_account_status(
    session: AsyncSession,
    *,
    wallet_account_id: uuid.UUID,
    status: WalletAccountStatus | str,
) -> None:
    await session.execute(
        update(WalletAccount)
        .where(WalletAccount.id == wallet_account_id)
        .values(status=_wallet_status_value(status), updated_at=datetime.now())
    )


async def update_wallet_account_meta(
    session: AsyncSession,
    *,
    wallet_account_id: uuid.UUID,
    enabled_wallets: dict[str, Any] | None = None,
    ip_whitelist: list[str] | None = None,
    purpose: str | None = None,
) -> None:
    values: dict[str, Any] = {"updated_at": datetime.now()}
    if enabled_wallets is not None:
        values["enabled_wallets"] = enabled_wallets
    if ip_whitelist is not None:
        values["ip_whitelist"] = ip_whitelist
    if purpose is not None:
        values["purpose"] = purpose
    if len(values) == 1:
        return
    await session.execute(
        update(WalletAccount)
        .where(WalletAccount.id == wallet_account_id)
        .values(**values)
    )


async def delete_wallet_account(
    session: AsyncSession, *, wallet_account_id: uuid.UUID
) -> None:
    await session.execute(
        delete(WalletAccount).where(WalletAccount.id == wallet_account_id)
    )


# ---------------------------------------------------------------------------
# StrategyAllocation (per-job capital budget)
# ---------------------------------------------------------------------------


async def get_strategy_allocation(
    session: AsyncSession, *, job_id: uuid.UUID
) -> StrategyAllocation | None:
    result = await session.execute(
        select(StrategyAllocation).where(StrategyAllocation.job_id == job_id)
    )
    return result.scalar_one_or_none()


async def list_strategy_allocations_for_wallet(
    session: AsyncSession, *, wallet_account_id: uuid.UUID
) -> list[StrategyAllocation]:
    result = await session.execute(
        select(StrategyAllocation).where(
            StrategyAllocation.wallet_account_id == wallet_account_id
        )
    )
    return list(result.scalars().all())


async def upsert_strategy_allocation(  # noqa: PLR0913 — each kwarg maps to a distinct table column
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    wallet_account_id: uuid.UUID,
    allocated_usdt: float,
    allocation_mode: AllocationMode | str = AllocationMode.FIXED_USDT,
    max_drawdown_pct: float | None = None,
) -> StrategyAllocation:
    mode_value = (
        allocation_mode.value if isinstance(allocation_mode, AllocationMode) else allocation_mode
    )
    now = datetime.now()
    stmt = (
        insert(StrategyAllocation)
        .values(
            job_id=job_id,
            wallet_account_id=wallet_account_id,
            allocation_mode=mode_value,
            allocated_usdt=allocated_usdt,
            reserved_usdt=0.0,
            max_drawdown_pct=max_drawdown_pct,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_strategy_alloc_job",
            set_={
                "wallet_account_id": wallet_account_id,
                "allocation_mode": mode_value,
                "allocated_usdt": allocated_usdt,
                "max_drawdown_pct": max_drawdown_pct,
                "updated_at": now,
            },
        )
        .returning(StrategyAllocation)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    refreshed = await session.execute(
        select(StrategyAllocation).where(StrategyAllocation.job_id == job_id)
    )
    return refreshed.scalar_one()


async def adjust_strategy_allocation_reserved(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    delta_usdt: float,
) -> float | None:
    """Atomically bump ``reserved_usdt`` by ``delta_usdt``.

    Returns the new ``reserved_usdt`` value, or ``None`` if no row exists.
    The pre-trade gate uses this to reserve capital on order intent and
    release it after fills/cancels.
    """
    result = await session.execute(
        update(StrategyAllocation)
        .where(StrategyAllocation.job_id == job_id)
        .values(
            reserved_usdt=StrategyAllocation.reserved_usdt + delta_usdt,
            updated_at=datetime.now(),
        )
        .returning(StrategyAllocation.reserved_usdt)
    )
    return result.scalar_one_or_none()


async def reset_strategy_allocation_reserved(
    session: AsyncSession, *, job_id: uuid.UUID, reserved_usdt: float
) -> None:
    await session.execute(
        update(StrategyAllocation)
        .where(StrategyAllocation.job_id == job_id)
        .values(reserved_usdt=reserved_usdt, updated_at=datetime.now())
    )


async def delete_strategy_allocation(
    session: AsyncSession, *, job_id: uuid.UUID
) -> None:
    await session.execute(
        delete(StrategyAllocation).where(StrategyAllocation.job_id == job_id)
    )


# ---------------------------------------------------------------------------
# WalletTransfer (audit log)
# ---------------------------------------------------------------------------


def _transfer_status_value(status: WalletTransferStatus | str) -> str:
    return status.value if isinstance(status, WalletTransferStatus) else str(status)


async def create_wallet_transfer(  # noqa: PLR0913 — every field is a distinct dimension of the transfer
    session: AsyncSession,
    *,
    user_id: str,
    from_wallet_account_id: uuid.UUID | None,
    to_wallet_account_id: uuid.UUID | None,
    from_wallet_type: str,
    to_wallet_type: str,
    asset: str,
    amount: float,
    reason: str,
    client_tran_id: str,
    status: WalletTransferStatus | str = WalletTransferStatus.PENDING,
) -> WalletTransfer:
    transfer = WalletTransfer(
        user_id=user_id,
        from_wallet_account_id=from_wallet_account_id,
        to_wallet_account_id=to_wallet_account_id,
        from_wallet_type=from_wallet_type,
        to_wallet_type=to_wallet_type,
        asset=asset,
        amount=amount,
        reason=reason,
        status=_transfer_status_value(status),
        client_tran_id=client_tran_id,
    )
    session.add(transfer)
    await session.flush()
    return transfer


async def mark_wallet_transfer_succeeded(
    session: AsyncSession,
    *,
    transfer_id: uuid.UUID,
    binance_tran_id: str | None,
) -> None:
    await session.execute(
        update(WalletTransfer)
        .where(WalletTransfer.id == transfer_id)
        .values(
            status=WalletTransferStatus.SUCCEEDED.value,
            binance_tran_id=binance_tran_id,
            completed_at=datetime.now(),
        )
    )


async def mark_wallet_transfer_failed(
    session: AsyncSession,
    *,
    transfer_id: uuid.UUID,
    error_message: str,
) -> None:
    await session.execute(
        update(WalletTransfer)
        .where(WalletTransfer.id == transfer_id)
        .values(
            status=WalletTransferStatus.FAILED.value,
            error_message=error_message[:1000],
            completed_at=datetime.now(),
        )
    )


async def get_wallet_transfer_by_client_id(
    session: AsyncSession, *, client_tran_id: str
) -> WalletTransfer | None:
    result = await session.execute(
        select(WalletTransfer).where(WalletTransfer.client_tran_id == client_tran_id)
    )
    return result.scalar_one_or_none()


async def list_wallet_transfers(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 50,
) -> list[WalletTransfer]:
    result = await session.execute(
        select(WalletTransfer)
        .where(WalletTransfer.user_id == user_id)
        .order_by(WalletTransfer.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
