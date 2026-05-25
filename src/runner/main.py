from __future__ import annotations

import asyncio
from pathlib import Path

from control.alembic_upgrade import run_alembic_upgrade_head
from control.db import create_async_engine, create_session_maker, init_db
from runner.worker import RunnerWorker
from settings import get_settings


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


async def _amain() -> None:
    settings = get_settings()
    # Run Alembic only on containers responsible for it. With RUNNER_ROLE=live
    # we explicitly skip migrations so the LIVE container starts quickly and
    # cannot race the BACKTEST container on schema locks. Default 'both' keeps
    # the legacy single-runner behaviour.
    role = str(settings.runner_role or "both").strip().lower()
    role_runs_alembic = role in ("backtest", "both")
    if settings.auto_alembic_upgrade and role_runs_alembic:
        print(f"[runner] applying database migrations (alembic upgrade head, role={role})...")
        await asyncio.to_thread(run_alembic_upgrade_head)
    elif settings.auto_alembic_upgrade:
        print(f"[runner] skipping alembic upgrade (role={role}); migrations owned by sibling container")
    engine = create_async_engine(settings.effective_database_url)
    await init_db(engine)
    session_maker = create_session_maker(engine)

    worker = RunnerWorker(
        repo_root=_repo_root(),
        session_maker=session_maker,
        poll_interval_ms=settings.runner_poll_interval_ms,
        live_concurrency=settings.runner_live_concurrency,
        role=settings.runner_role,
    )

    print(
        f"[runner] starting worker role={role} (user-specific Binance keys mode, "
        f"live_concurrency={settings.runner_live_concurrency})"
    )
    await worker.run_forever()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
