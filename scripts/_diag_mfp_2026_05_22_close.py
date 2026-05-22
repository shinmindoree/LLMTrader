"""Diagnose why the live MFP job didn't issue the 2026-05-22 03:30 KST close
that the backtest produced.

Bar of interest:
  - open_time  = 2026-05-21 18:15:00 UTC = 1779386700000 ms
  - close_time = 2026-05-21 18:30:00 UTC = 1779387600000 ms
  - KST close  = 2026-05-22 03:30:00 KST
  - Backtest trade timestamp = 03:29:59 KST (close - 1s)

Queries:
  1. Current/most recent LIVE MFP job.
  2. MFP_BAR / MFP_FLAT / MFP_DATA_GAP / MFP_ENTER_* / MFP_CTX_RESYNC events
     around ts=1779386700000 (the strategy uses bar OPEN time as `ts` payload).
  3. Trades table for any recent close on this job.
  4. The latest few MFP audit events overall, to see how far the live job
     has advanced in real time.
"""
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import text

from settings import get_settings
from control.db import create_async_engine, create_session_maker


BAR_OPEN_MS = 1779386700000     # 2026-05-21 18:15:00 UTC
BAR_CLOSE_MS = 1779387600000    # 2026-05-21 18:30:00 UTC


def fmt_ts(ms: int | None) -> str:
    if ms is None:
        return "—"
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    SessionLocal = create_session_maker(engine)

    async with SessionLocal() as session:
        # 1) Most recent LIVE MFP job
        res = await session.execute(text(
            """
            SELECT job_id, status, strategy_path, created_at,
                   started_at, ended_at, live_heartbeat_at
            FROM jobs
            WHERE type = 'LIVE'
              AND strategy_path ILIKE '%multi_factor_portfolio%'
            ORDER BY created_at DESC
            LIMIT 5
            """
        ))
        jobs = res.fetchall()
        if not jobs:
            print("[diag] no LIVE MFP job found")
            await engine.dispose()
            return
        print(f"[diag] last {len(jobs)} LIVE MFP jobs:")
        for j in jobs:
            print(f"  job_id={j[0]} status={j[1]} created={j[3]} hb={j[6]}")
        job_id = jobs[0][0]
        job_status = jobs[0][1]
        print(f"\n[diag] using job_id={job_id} status={job_status}")
        print(f"[diag] target bar: open={fmt_ts(BAR_OPEN_MS)} close={fmt_ts(BAR_CLOSE_MS)}")

        # 2) Latest 30 MFP audit events for this job (any kind)
        res = await session.execute(text(
            """
            SELECT ts, kind, level, message, payload_json
            FROM job_events
            WHERE job_id = :job_id
              AND (message LIKE 'MFP_%'
                   OR kind = 'audit')
            ORDER BY ts DESC
            LIMIT 30
            """
        ), {"job_id": job_id})
        rows = res.fetchall()
        print(f"\n[diag] latest {len(rows)} events (ts DESC):")
        for r in rows:
            ts, kind, level, msg, payload = r
            payload_str = ""
            if payload:
                # extract the most useful fields concisely
                if isinstance(payload, dict):
                    keys = ["ts", "target", "long_legs", "short_legs",
                            "committed_side", "prev_side", "changed",
                            "delta", "total", "cached_side", "actual_side",
                            "target_side", "active"]
                    extracted = {k: payload[k] for k in keys if k in payload}
                    payload_str = json.dumps(extracted, default=str)[:200]
            print(f"  {ts.strftime('%m-%d %H:%M:%S')} {msg:24s} {payload_str}")

        # 3) Events where payload ts is at/around the target bar
        # The strategy stores ts as bar OPEN time in payload_json.
        res = await session.execute(text(
            """
            SELECT ts, message, payload_json
            FROM job_events
            WHERE job_id = :job_id
              AND payload_json ? 'ts'
              AND (payload_json->>'ts')::bigint BETWEEN :lo AND :hi
            ORDER BY ts ASC
            """
        ), {
            "job_id": job_id,
            "lo": BAR_OPEN_MS - 30 * 60 * 1000,   # 30 min window
            "hi": BAR_OPEN_MS + 30 * 60 * 1000,
        })
        rows = res.fetchall()
        print(f"\n[diag] events with payload.ts in [{fmt_ts(BAR_OPEN_MS - 1800000)} .. {fmt_ts(BAR_OPEN_MS + 1800000)}]")
        print(f"        ({len(rows)} rows)")
        for r in rows:
            ts, msg, payload = r
            bar_ts_ms = int(payload.get("ts", 0)) if payload else 0
            extra = ""
            if payload:
                for k in ("target", "long_legs", "short_legs",
                          "committed_side", "prev_side", "changed",
                          "delta", "total"):
                    if k in payload:
                        extra += f" {k}={payload[k]}"
            print(f"  {ts.strftime('%m-%d %H:%M:%S')} bar_ts={fmt_ts(bar_ts_ms)} {msg}{extra}")

        # 4) Last 20 MFP_BAR / MFP_FLAT / MFP_ENTER_* events for this job
        res = await session.execute(text(
            """
            SELECT ts, message, payload_json
            FROM job_events
            WHERE job_id = :job_id
              AND message IN ('MFP_BAR', 'MFP_FLAT',
                              'MFP_ENTER_LONG', 'MFP_ENTER_SHORT',
                              'MFP_DATA_GAP', 'MFP_CTX_RESYNC')
            ORDER BY ts DESC
            LIMIT 20
            """
        ), {"job_id": job_id})
        rows = res.fetchall()
        print(f"\n[diag] last {len(rows)} MFP_BAR/FLAT/ENTER/DATA_GAP events:")
        for r in rows:
            ts, msg, payload = r
            bar_ts_ms = int(payload.get("ts", 0)) if payload else 0
            extra = ""
            if payload:
                for k in ("target", "long_legs", "short_legs",
                          "committed_side", "prev_side", "changed",
                          "delta"):
                    if k in payload:
                        extra += f" {k}={payload[k]}"
            print(f"  {ts.strftime('%m-%d %H:%M:%S')} bar_ts={fmt_ts(bar_ts_ms)} {msg}{extra}")

        # 5) Trades for this job sorted by ts DESC
        res = await session.execute(text(
            """
            SELECT id, ts, quantity, price, realized_pnl, raw_json
            FROM trades
            WHERE job_id = :job_id
            ORDER BY ts DESC
            LIMIT 5
            """
        ), {"job_id": job_id})
        rows = res.fetchall()
        print(f"\n[diag] last {len(rows)} trades:")
        for r in rows:
            tid, ts, qty, price, pnl, raw = r
            side = (raw or {}).get("side", "?")
            reason = (raw or {}).get("reason", "?")
            print(f"  id={tid} ts={ts} side={side} px={price} qty={qty} pnl={pnl} reason={reason}")

        # 6) Job heartbeat freshness
        res = await session.execute(text(
            "SELECT live_heartbeat_at, NOW() AT TIME ZONE 'utc' - live_heartbeat_at "
            "FROM jobs WHERE job_id = :job_id"
        ), {"job_id": job_id})
        hb_row = res.fetchone()
        if hb_row:
            print(f"\n[diag] heartbeat={hb_row[0]} age={hb_row[1]}")

    await engine.dispose()


asyncio.run(main())
