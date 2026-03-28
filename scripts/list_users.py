"""Quick script to list all registered users from the database."""
import asyncio
import sys

sys.path.insert(0, "src")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main():
    import os

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        db_url = (
            "postgresql+asyncpg://llmtraderadmin:LlmTr4d3r!Pg2026"
            "@fdpo-test-pgdb.postgres.database.azure.com:5432/postgres?ssl=require"
        )
    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT user_id, email, display_name, plan, email_verified, created_at "
                "FROM user_profiles ORDER BY created_at DESC"
            )
        )
        rows = result.fetchall()
        if not rows:
            print("No users found.")
        else:
            header = (
                f"{'user_id':<40} {'email':<35} {'name':<20} "
                f"{'plan':<8} {'verified':<10} created_at"
            )
            print(header)
            print("-" * 140)
            for r in rows:
                print(
                    f"{str(r[0]):<40} {str(r[1]):<35} {str(r[2]):<20} "
                    f"{str(r[3]):<8} {str(r[4]):<10} {str(r[5])}"
                )
        print(f"\nTotal: {len(rows)} users")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
