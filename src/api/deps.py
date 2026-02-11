from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from settings import get_settings

_session_maker_ref: async_sessionmaker[AsyncSession] | None = None


def set_session_maker(sm: async_sessionmaker[AsyncSession]) -> None:
    global _session_maker_ref  # noqa: PLW0603
    _session_maker_ref = sm


def _get_session_maker() -> async_sessionmaker[AsyncSession]:
    if _session_maker_ref is None:
        raise RuntimeError("session_maker not initialized")
    return _session_maker_ref


@dataclass(slots=True)
class AuthenticatedUser:
    user_id: str
    email: str | None = None
    provider: str = "admin"
    plan: str = "free"
    _extra: dict[str, str] = field(default_factory=dict, repr=False)


def _extract_bearer_token(authorization: str | None) -> str:
    value = (authorization or "").strip()
    if not value:
        return ""
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


async def _verify_supabase_user(access_token: str) -> AuthenticatedUser:
    settings = get_settings()
    supabase = settings.supabase_auth
    url = (supabase.url or "").strip().rstrip("/")
    anon_key = (supabase.anon_key or "").strip()
    if not url or not anon_key:
        raise HTTPException(status_code=500, detail="SUPABASE_URL/SUPABASE_ANON_KEY is not configured")

    headers = {
        "authorization": f"Bearer {access_token}",
        "apikey": anon_key,
    }
    endpoint = f"{url}/auth/v1/user"
    timeout = max(1.0, float(supabase.auth_timeout_seconds))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(endpoint, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Supabase auth request failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Supabase auth failed with {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid Supabase auth response") from exc

    user_id = str(payload.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email = str(payload.get("email") or "").strip() or None
    return AuthenticatedUser(user_id=user_id, email=email, provider="supabase")


async def _ensure_user_profile(user: AuthenticatedUser) -> AuthenticatedUser:
    from control.models import UserProfile

    sm = _get_session_maker()
    async with sm() as session:
        stmt = (
            insert(UserProfile)
            .values(user_id=user.user_id, email=user.email or "", display_name="")
            .on_conflict_do_nothing(index_elements=[UserProfile.user_id])
        )
        await session.execute(stmt)
        await session.commit()

        result = await session.execute(
            select(UserProfile.plan).where(UserProfile.user_id == user.user_id)
        )
        plan = result.scalar_one_or_none() or "free"
        user.plan = plan
    return user


async def require_auth(
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    settings = get_settings()
    supabase = settings.supabase_auth
    supabase_enabled = supabase.enabled or bool((supabase.url or "").strip())

    if supabase_enabled:
        token = _extract_bearer_token(authorization)
        if token:
            user = await _verify_supabase_user(token)
            return await _ensure_user_profile(user)
        if not supabase.allow_admin_fallback:
            raise HTTPException(status_code=401, detail="Unauthorized")

    expected = settings.admin_token.strip()
    if not expected:
        detail = "ADMIN_TOKEN is not configured"
        if supabase_enabled:
            detail = "SUPABASE token is missing and ADMIN fallback is not configured"
        raise HTTPException(status_code=500, detail=detail)
    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    admin_user = AuthenticatedUser(user_id="admin", email=None, provider="admin")
    return await _ensure_user_profile(admin_user)


# Backward compatibility alias
require_admin = require_auth


async def get_db_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session
