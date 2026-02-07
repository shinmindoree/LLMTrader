from __future__ import annotations

import asyncio
from pathlib import Path

from control.db import create_async_engine, create_session_maker, init_db
from runner.worker import RunnerWorker
from settings import get_settings


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


async def _amain() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.effective_database_url)
    await init_db(engine)
    session_maker = create_session_maker(engine)

    worker = RunnerWorker(
        repo_root=_repo_root(),
        session_maker=session_maker,
        poll_interval_ms=settings.runner_poll_interval_ms,
    )
    await worker.run_forever()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
