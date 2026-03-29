from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine as _cae

from control.models import Base


def create_async_engine(database_url: str) -> AsyncEngine:
    # PgBouncer transaction mode requires disabling prepared statements
    connect_args: dict[str, object] = {}
    if ":6432" in database_url or "pgbouncer=true" in database_url.lower():
        connect_args["prepared_statement_cache_size"] = 0

    return _cae(
        database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_timeout=30,
        pool_recycle=300,
        connect_args=connect_args,
    )


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def session_scope(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session
