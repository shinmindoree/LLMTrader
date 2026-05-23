"""Round 3: cross-check MFP_INIT vs JOB_REQUEUED timestamps to confirm whether
the 5/21 09:37 MFP_INIT was a true container restart or just a re-warmup.
"""
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import text

from settings import get_settings
from control.db import create_async_engine, create_session_maker

JOB_ID = "ad00773e-ec98-4961-90d7-602c36ea688c"


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    SessionLocal = create_session_maker(engine)

    async with SessionLocal() as session:
        # All JOB_* events for this job
        res = await session.execute(text(
            """
            SELECT ts, message, payload_json
            FROM job_events
            WHERE job_id = :job_id
              AND message IN ('JOB_CREATED','JOB_REQUEUED','JOB_RUNNING',
                              'JOB_STOPPED','JOB_FAILED','LIVE_START',
                              'MFP_INIT','MFP_WARMUP')
            ORDER BY ts ASC
            """
        ), {"job_id": JOB_ID})
        rows = res.fetchall()
        print(f"[diag] {len(rows)} lifecycle events:")
        for r in rows:
            ts, msg, payload = r
            ps = json.dumps(payload, default=str)[:160] if payload else "—"
            print(f"  {ts}  {msg:<16s} {ps}")

    await engine.dispose()


asyncio.run(main())
