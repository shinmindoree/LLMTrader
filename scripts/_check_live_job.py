import asyncio
import json
from settings import get_settings
from control.database import create_engine_and_session
from sqlalchemy import text

async def main():
    settings = get_settings()
    engine, SessionLocal = create_engine_and_session(settings.database_url)
    async with SessionLocal() as session:
        result = await session.execute(text(
            "SELECT job_id, status, result_json FROM jobs "
            "WHERE type = 'LIVE' AND status = 'RUNNING' "
            "ORDER BY created_at DESC LIMIT 1"
        ))
        row = result.fetchone()
        if row:
            print(f"job_id: {row[0]}")
            print(f"status: {row[1]}")
            print(f"result_json: {json.dumps(row[2], indent=2) if row[2] else 'NULL'}")
        else:
            print("No running LIVE job found")
    await engine.dispose()

asyncio.run(main())
