"""김프 시계열 1분 스냅샷 기록 + 통계 조회.

백엔드 부팅 시 ``start_collector(session_maker)`` 로 1분 간격 스냅샷 적재
백그라운드 태스크를 등록한다. 외부 환율/공개 시세 API 실패 시 로깅만 하고
다음 사이클로 넘어가서 엔진/API 가용성을 해치지 않는다.

조회 헬퍼:
- ``window_stats(session, symbol, days)`` : 평균·표준편차
- ``recent_series(session, symbol, points, since)`` : 차트용 시계열
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from statistics import StatisticsError, mean, pstdev

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control.models import KimpSnapshot
from live.kimp_calculator import DEFAULT_SYMBOLS, compute_kimp_snapshot

_log = logging.getLogger("llmtrader.kimp_history")

SNAPSHOT_INTERVAL_SEC = 60.0


async def collect_once(
    session_maker: async_sessionmaker[AsyncSession],
    symbols: list[str] | None = None,
) -> int:
    """한 번의 김프 스냅샷을 ``kimp_snapshots`` 테이블에 적재한다.

    중복 ``(symbol, ts)`` 는 INSERT … ON CONFLICT DO NOTHING 으로 무시된다.
    적재된 row 수를 반환한다 (오류 시 0).
    """
    try:
        snapshot = await compute_kimp_snapshot(symbols)
    except Exception as exc:  # noqa: BLE001
        _log.warning("compute_kimp_snapshot failed: %s", exc)
        return 0

    if not snapshot.rows:
        if snapshot.errors:
            _log.info("kimp snapshot empty: %s", snapshot.errors[:3])
        return 0

    ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    payload = [
        {
            "symbol": row.symbol,
            "ts": ts,
            "upbit_krw_price": row.upbit_krw_price,
            "binance_usdt_price": row.binance_usdt_price,
            "usd_krw_rate": row.usd_krw_rate,
            "kimp_pct": row.kimp_pct,
            "fx_source": row.fx_source,
        }
        for row in snapshot.rows
    ]

    inserted = 0
    try:
        async with session_maker() as session:
            stmt = (
                pg_insert(KimpSnapshot)
                .values(payload)
                .on_conflict_do_nothing(index_elements=["symbol", "ts"])
            )
            result = await session.execute(stmt)
            await session.commit()
            inserted = int(result.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        _log.warning("kimp snapshot persist failed: %s", exc)
        return 0

    return inserted


async def _collector_loop(session_maker: async_sessionmaker[AsyncSession]) -> None:
    _log.info("kimp snapshot collector started (interval=%.0fs)", SNAPSHOT_INTERVAL_SEC)
    while True:
        try:
            await collect_once(session_maker)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("kimp collector iteration failed")
        await asyncio.sleep(SNAPSHOT_INTERVAL_SEC)


_collector_task: asyncio.Task[None] | None = None


def start_collector(session_maker: async_sessionmaker[AsyncSession]) -> asyncio.Task[None]:
    """이미 동작 중이면 기존 task 를 반환, 아니면 새로 띄운다."""
    global _collector_task
    if _collector_task is None or _collector_task.done():
        _collector_task = asyncio.create_task(
            _collector_loop(session_maker), name="kimp_snapshot_collector"
        )
    return _collector_task


async def stop_collector() -> None:
    global _collector_task
    if _collector_task is not None and not _collector_task.done():
        _collector_task.cancel()
        try:
            await _collector_task
        except (asyncio.CancelledError, Exception):
            pass
    _collector_task = None


async def window_stats(
    session: AsyncSession, symbol: str, days: int
) -> dict[str, float | int | None]:
    """심볼별 윈도우 통계: 평균/표준편차/표본수 + 직전 값."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(KimpSnapshot.kimp_pct)
        .where(KimpSnapshot.symbol == symbol, KimpSnapshot.ts >= since)
        .order_by(KimpSnapshot.ts.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    values = [float(v) for v in rows if v is not None and not math.isnan(v)]
    if not values:
        return {"n": 0, "mean": None, "std": None, "last": None}
    try:
        std = pstdev(values) if len(values) >= 2 else 0.0
    except StatisticsError:
        std = 0.0
    return {
        "n": len(values),
        "mean": mean(values),
        "std": std,
        "last": values[-1],
    }


async def recent_series(
    session: AsyncSession,
    symbol: str,
    since: datetime,
    max_points: int = 2000,
) -> list[tuple[datetime, float]]:
    """``since`` 이후의 시계열을 ts 오름차순으로 반환. ``max_points`` 초과 시 균등 다운샘플."""
    stmt = (
        select(KimpSnapshot.ts, KimpSnapshot.kimp_pct)
        .where(KimpSnapshot.symbol == symbol, KimpSnapshot.ts >= since)
        .order_by(KimpSnapshot.ts.asc())
    )
    rows = (await session.execute(stmt)).all()
    series: list[tuple[datetime, float]] = [(ts, float(v)) for ts, v in rows]
    if len(series) <= max_points:
        return series
    step = max(1, len(series) // max_points)
    return series[::step][:max_points]


async def last_n_rows(
    session: AsyncSession, symbol: str, n: int = 1
) -> list[KimpSnapshot]:
    stmt = (
        select(KimpSnapshot)
        .where(KimpSnapshot.symbol == symbol)
        .order_by(desc(KimpSnapshot.ts))
        .limit(n)
    )
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "DEFAULT_SYMBOLS",
    "SNAPSHOT_INTERVAL_SEC",
    "collect_once",
    "start_collector",
    "stop_collector",
    "window_stats",
    "recent_series",
    "last_n_rows",
]
