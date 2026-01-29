from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control.enums import EventKind, JobStatus, JobType
from control.repo import (
    append_event,
    claim_next_pending_job,
    finalize_orphaned_jobs,
    get_job,
    set_job_finished,
)
from runner.event_sink import DbEventSink
from runner.executors.backtest_executor import run_backtest
from runner.executors.live_executor import run_live


class RunnerWorker:
    def __init__(
        self,
        *,
        repo_root: Path,
        session_maker: async_sessionmaker[AsyncSession],
        poll_interval_ms: int,
    ) -> None:
        self._repo_root = repo_root
        self._session_maker = session_maker
        self._poll_interval = max(50, poll_interval_ms) / 1000.0

    async def run_forever(self) -> None:
        # On runner startup, finalize any jobs left RUNNING/STOP_REQUESTED from a previous crash/restart.
        # In MVP mode we do not attempt to resume them; we mark them completed so new jobs can run safely.
        async with self._session_maker() as session:
            counts = await finalize_orphaned_jobs(session, reason="runner_startup")
            if counts.get("finalized_failed") or counts.get("finalized_stopped"):
                print(f"[runner] finalized orphaned jobs: {counts}")
            await session.commit()

        await asyncio.gather(
            self._run_loop(JobType.BACKTEST),
            self._run_loop(JobType.LIVE),
        )

    async def _run_loop(self, job_type: JobType) -> None:
        while True:
            async with self._session_maker() as session:
                job = await claim_next_pending_job(session, job_type=job_type)
                if not job:
                    await session.commit()
                    await asyncio.sleep(self._poll_interval)
                    continue

                await append_event(
                    session,
                    job_id=job.job_id,
                    kind=EventKind.STATUS,
                    message="JOB_RUNNING",
                    payload_json={"started_at": datetime.now().isoformat()},
                )
                await session.commit()

            await self._run_job(job_id=job.job_id)

    async def _run_job(self, *, job_id: uuid.UUID) -> None:
        should_stop = asyncio.Event()

        async def stop_poller() -> None:
            while True:
                try:
                    async with self._session_maker() as session:
                        job = await get_job(session, job_id)
                        if job and str(job.status) == str(JobStatus.STOP_REQUESTED):
                            should_stop.set()
                            return
                except Exception as exc:  # noqa: BLE001
                    # Never let stop polling silently die.
                    # If the poller crashes, LIVE jobs can get stuck in STOP_REQUESTED forever.
                    print(f"[runner] stop-poller error job_id={job_id}: {type(exc).__name__}: {exc}")
                await asyncio.sleep(0.5)

        stop_task = asyncio.create_task(stop_poller(), name=f"stop-poller:{job_id}")

        async with self._session_maker() as session:
            job = await get_job(session, job_id)
            if not job:
                stop_task.cancel()
                return
            job_type = JobType(str(job.type))
            strategy_path = job.strategy_path
            config = dict(job.config_json or {})

        sink = DbEventSink(session_maker=self._session_maker, job_id=job_id)
        sink.start()

        try:
            result: dict[str, Any]
            if job_type == JobType.BACKTEST:
                result = await run_backtest(
                    repo_root=self._repo_root,
                    strategy_path=strategy_path,
                    config=config,
                    sink=sink,
                    should_stop=should_stop,
                )
            else:
                result = await run_live(
                    repo_root=self._repo_root,
                    strategy_path=strategy_path,
                    config=config,
                    sink=sink,
                    should_stop=should_stop,
                )

            status = JobStatus.STOPPED if should_stop.is_set() else JobStatus.SUCCEEDED
            async with self._session_maker() as session:
                await set_job_finished(session, job_id=job_id, status=status, result_json=result)
                await append_event(
                    session,
                    job_id=job_id,
                    kind=EventKind.STATUS,
                    message="JOB_FINISHED",
                    payload_json={"status": str(status)},
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            if should_stop.is_set():
                async with self._session_maker() as session:
                    await set_job_finished(
                        session,
                        job_id=job_id,
                        status=JobStatus.STOPPED,
                        result_json={"stopped": True},
                        error=None,
                    )
                    await append_event(
                        session,
                        job_id=job_id,
                        kind=EventKind.STATUS,
                        message="JOB_STOPPED",
                        payload_json={"reason": "stop_requested"},
                    )
                    await session.commit()
                return
            async with self._session_maker() as session:
                await set_job_finished(session, job_id=job_id, status=JobStatus.FAILED, error=str(exc))
                await append_event(
                    session,
                    job_id=job_id,
                    kind=EventKind.STATUS,
                    message="JOB_FAILED",
                    payload_json={"error": str(exc)},
                )
                await session.commit()
        finally:
            stop_task.cancel()
            await sink.stop()
