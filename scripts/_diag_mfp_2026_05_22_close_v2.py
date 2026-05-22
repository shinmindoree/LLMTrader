"""Round 2: dig into the specific 5/21 18:15-18:30 UTC window using audit ts
(server-side), and dump raw payload_json so we can see what's actually stored.
"""
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import text

from settings import get_settings
from control.db import create_async_engine, create_session_maker


JOB_ID = "ad00773e-ec98-4961-90d7-602c36ea688c"
# Bar of interest: open=5/21 18:15 UTC, close=5/21 18:30 UTC
# Live emits on_bar AFTER close, so audit ts should be 18:30:00 - 18:30:30 UTC.
WINDOW_START = datetime(2026, 5, 21, 18, 0, 0, tzinfo=timezone.utc)
WINDOW_END   = datetime(2026, 5, 21, 19, 0, 0, tzinfo=timezone.utc)
RESTART_LO   = datetime(2026, 5, 22,  8,  0, 0, tzinfo=timezone.utc)
RESTART_HI   = datetime(2026, 5, 22,  8, 20, 0, tzinfo=timezone.utc)
SINCE_521    = datetime(2026, 5, 21,  0,  0, 0, tzinfo=timezone.utc)


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    SessionLocal = create_session_maker(engine)

    async with SessionLocal() as session:
        # (a) All events for this job in the 18:00-19:00 UTC window
        res = await session.execute(text(
            """
            SELECT ts, kind, level, message, payload_json
            FROM job_events
            WHERE job_id = :job_id
              AND ts >= :lo
              AND ts <= :hi
            ORDER BY ts ASC
            """
        ), {"job_id": JOB_ID, "lo": WINDOW_START, "hi": WINDOW_END})
        rows = res.fetchall()
        print(f"[diag] {len(rows)} events between {WINDOW_START} and {WINDOW_END}:")
        for r in rows:
            ts, kind, level, msg, payload = r
            payload_str = json.dumps(payload, default=str)[:280] if payload else "—"
            print(f"  {ts.strftime('%m-%d %H:%M:%S.%f')[:-3]} kind={kind} msg={msg}")
            print(f"      payload={payload_str}")

        # (b) Detect job restarts: all MFP_INIT events
        res = await session.execute(text(
            """
            SELECT ts, payload_json
            FROM job_events
            WHERE job_id = :job_id AND message = 'MFP_INIT'
            ORDER BY ts ASC
            """
        ), {"job_id": JOB_ID})
        rows = res.fetchall()
        print(f"\n[diag] all MFP_INIT events (= restart points):")
        for r in rows:
            ts, payload = r
            keys = ["mode", "seed_last_ts", "live_tail_ts", "data_rows_15m",
                    "gap_fill_error", "data_gap_counts"]
            extracted = {k: payload.get(k) for k in keys if payload and k in payload}
            print(f"  {ts}  {json.dumps(extracted, default=str)[:240]}")

        # (c) Counts by message type across the job's lifetime
        res = await session.execute(text(
            """
            SELECT message, COUNT(*)
            FROM job_events
            WHERE job_id = :job_id
            GROUP BY message
            ORDER BY COUNT(*) DESC
            """
        ), {"job_id": JOB_ID})
        rows = res.fetchall()
        print(f"\n[diag] event counts by message:")
        for r in rows:
            print(f"  {r[0]:<32s} {r[1]}")

        # (d) MFP_FLAT / MFP_ENTER_* events on 5/21
        res = await session.execute(text(
            """
            SELECT ts, message, payload_json
            FROM job_events
            WHERE job_id = :job_id
              AND message IN ('MFP_FLAT', 'MFP_ENTER_LONG', 'MFP_ENTER_SHORT',
                              'MFP_DATA_GAP', 'MFP_ENTRY_LOST', 'MFP_CTX_RESYNC')
              AND ts >= :since
            ORDER BY ts ASC
            """
        ), {"job_id": JOB_ID, "since": SINCE_521})
        rows = res.fetchall()
        print(f"\n[diag] MFP_FLAT / ENTER_* / DATA_GAP events since 5/21:")
        for r in rows:
            ts, msg, payload = r
            kept = ["ts", "target", "long_legs", "short_legs", "committed_side",
                    "prev_side", "delta", "total", "cached_side", "actual_side"]
            ex = {k: payload[k] for k in kept if payload and k in payload}
            print(f"  {ts}  {msg:<24s} {json.dumps(ex, default=str)[:240]}")

        # (e) Look at a sample MFP_BAR payload to verify structure
        res = await session.execute(text(
            """
            SELECT ts, payload_json
            FROM job_events
            WHERE job_id = :job_id AND message = 'MFP_BAR'
            ORDER BY ts DESC
            LIMIT 3
            """
        ), {"job_id": JOB_ID})
        rows = res.fetchall()
        print(f"\n[diag] sample MFP_BAR payloads (latest 3):")
        for r in rows:
            ts, payload = r
            print(f"  {ts}  {json.dumps(payload, default=str)[:300]}")

        # (f) The exact moment before/after the restart at 08:09:34 UTC
        res = await session.execute(text(
            """
            SELECT ts, message, payload_json
            FROM job_events
            WHERE job_id = :job_id
              AND ts >= :lo
              AND ts <= :hi
            ORDER BY ts ASC
            """
        ), {"job_id": JOB_ID, "lo": RESTART_LO, "hi": RESTART_HI})
        rows = res.fetchall()
        print(f"\n[diag] events around restart (8:00-8:20 UTC on 5/22):")
        for r in rows:
            ts, msg, payload = r
            ps = json.dumps(payload, default=str)[:200] if payload else "—"
            print(f"  {ts.strftime('%H:%M:%S.%f')[:-3]}  {msg:<24s} {ps}")

    await engine.dispose()


asyncio.run(main())
