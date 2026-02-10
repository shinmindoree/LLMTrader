from __future__ import annotations

import asyncio
from pathlib import Path

from control.db import create_async_engine, create_session_maker, init_db
from runner.account_snapshot import run_account_snapshot_loop
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

    tasks = [worker.run_forever()]

    binance_key = (settings.binance.api_key or "").strip()
    binance_secret = (settings.binance.api_secret or "").strip()
    if binance_key and binance_secret:
        tasks.append(
            run_account_snapshot_loop(
                session_maker=session_maker,
                api_key=binance_key,
                api_secret=binance_secret,
                base_url=(settings.binance.base_url or "").strip(),
            )
        )
        print("[runner] account snapshot loop enabled")
    else:
        print("[runner] BINANCE_API_KEY not set, account snapshot loop disabled")

    await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
