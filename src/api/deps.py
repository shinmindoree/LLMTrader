from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from settings import get_settings


@dataclass(slots=True)
class AuthenticatedUser:
    user_id: str
    email: str | None = None
    provider: str = "admin"


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


async def require_admin(
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    settings = get_settings()
    supabase = settings.supabase_auth
    supabase_enabled = supabase.enabled or bool((supabase.url or "").strip())

    if supabase_enabled:
        token = _extract_bearer_token(authorization)
        if token:
            return await _verify_supabase_user(token)
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
    return AuthenticatedUser(user_id="admin", email=None, provider="admin")


async def get_db_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session
