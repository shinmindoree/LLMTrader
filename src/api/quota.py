from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.plans import get_plan_limits
from control.enums import JobType
from control.repo import count_active_jobs, get_usage_count, increment_usage


def _current_period_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def check_job_quota(
    session: AsyncSession,
    *,
    user_id: str,
    plan: str,
    job_type: JobType,
) -> None:
    limits = get_plan_limits(plan)

    if job_type == JobType.LIVE:
        if limits.max_live_jobs <= 0:
            raise HTTPException(
                status_code=403,
                detail=f"'{plan}' 플랜은 라이브 트레이딩을 지원하지 않습니다. 업그레이드하세요.",
            )
        active = await count_active_jobs(session, user_id=user_id, job_type=JobType.LIVE)
        if active >= limits.max_live_jobs:
            raise HTTPException(
                status_code=403,
                detail=f"'{plan}' 플랜의 동시 라이브 한도({limits.max_live_jobs}개)에 도달했습니다.",
            )

    if job_type == JobType.BACKTEST:
        period = _current_period_key()
        count = await get_usage_count(session, user_id=user_id, action="backtest", period_key=period)
        if count >= limits.max_backtest_per_month:
            raise HTTPException(
                status_code=403,
                detail=f"이번 달 백테스트 한도({limits.max_backtest_per_month}회)에 도달했습니다. 업그레이드하세요.",
            )


async def record_job_usage(
    session: AsyncSession,
    *,
    user_id: str,
    job_type: JobType,
) -> None:
    period = _current_period_key()
    action = "live_start" if job_type == JobType.LIVE else "backtest"
    await increment_usage(session, user_id=user_id, action=action, period_key=period)


async def check_llm_generate_quota(
    session: AsyncSession,
    *,
    user_id: str,
    plan: str,
) -> None:
    limits = get_plan_limits(plan)
    period = _current_period_key()
    count = await get_usage_count(session, user_id=user_id, action="llm_generate", period_key=period)
    if count >= limits.max_llm_generate_per_month:
        raise HTTPException(
            status_code=403,
            detail=f"이번 달 LLM 전략 생성 한도({limits.max_llm_generate_per_month}회)에 도달했습니다. 업그레이드하세요.",
        )


async def record_llm_generate_usage(
    session: AsyncSession,
    *,
    user_id: str,
) -> None:
    period = _current_period_key()
    await increment_usage(session, user_id=user_id, action="llm_generate", period_key=period)
