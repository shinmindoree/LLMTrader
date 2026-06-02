#!/usr/bin/env python3
"""Execute queued deferred column drops (see control.deferred_drops).

Run *inside* a container that has database access, and only after an external
gate (cleanup-pending-column-drops.yml) has confirmed every container runs the
new schema. Idempotent: re-running is a no-op once the queue is drained.

The final line "PENDING_DROPS_DONE rc=<n>" is a sentinel the cron greps for to
distinguish a real success from a crashed `az containerapp exec`.
"""
from __future__ import annotations

import asyncio

from control.db import create_async_engine
from control.deferred_drops import process_pending_column_drops
from settings import get_settings


async def _amain() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.effective_database_url)
    try:
        async with engine.begin() as conn:
            results = await conn.run_sync(process_pending_column_drops)
    finally:
        await engine.dispose()

    if not results:
        print("No pending column drops.")
    else:
        for r in results:
            print(f"  {r.table_name}.{r.column_name}: {r.action}")
        print(f"Processed {len(results)} pending column drop(s).")
    return 0


def main() -> int:
    rc = asyncio.run(_amain())
    # Sentinel for the CI caller (printed last, after all work).
    print(f"PENDING_DROPS_DONE rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
