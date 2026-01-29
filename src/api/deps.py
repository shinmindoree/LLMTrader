from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from settings import get_settings


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    expected = settings.admin_token.strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not configured")
    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def get_db_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session

