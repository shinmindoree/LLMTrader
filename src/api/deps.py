from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncio

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from settings import get_settings

_session_maker_ref: async_sessionmaker[AsyncSession] | None = None
_jwk_client: PyJWKClient | None = None


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


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_admin_email(email: str | None) -> bool:
    settings = get_settings()
    expected = _normalize_email(settings.admin_email)
    return bool(expected) and _normalize_email(email) == expected


def _extract_bearer_token(authorization: str | None) -> str:
    value = (authorization or "").strip()
    if not value:
        return ""
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client  # noqa: PLW0603
    if _jwk_client is None:
        settings = get_settings()
        entra = settings.entra_auth
        jwks_url = entra.jwks_uri
        if not jwks_url:
            raise RuntimeError("Entra JWKS URI not configured")
        _jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
    return _jwk_client


async def _verify_entra_user(id_token: str) -> AuthenticatedUser:
    settings = get_settings()
    entra = settings.entra_auth

    if not entra.client_id:
        raise HTTPException(status_code=500, detail="ENTRA_CLIENT_ID is not configured")

    jwk_client = _get_jwk_client()

    try:
        signing_key = await asyncio.to_thread(jwk_client.get_signing_key_from_jwt, id_token)
        payload = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=entra.client_id,
            issuer=entra.issuer,
            options={"verify_exp": True},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

    user_id = str(payload.get("oid") or payload.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user identifier")

    email = str(payload.get("email") or payload.get("preferred_username") or "").strip() or None
    return AuthenticatedUser(user_id=user_id, email=email, provider="entra")


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
    entra = settings.entra_auth
    entra_enabled = entra.enabled or bool((entra.client_id or "").strip())

    if entra_enabled:
        token = _extract_bearer_token(authorization)
        if token:
            user = await _verify_entra_user(token)
            return await _ensure_user_profile(user)
        if not entra.allow_admin_fallback:
            raise HTTPException(status_code=401, detail="Unauthorized")

    expected = settings.admin_token.strip()
    if not expected:
        detail = "ADMIN_TOKEN is not configured"
        if entra_enabled:
            detail = "Entra token is missing and ADMIN fallback is not configured"
        raise HTTPException(status_code=500, detail=detail)
    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    admin_user = AuthenticatedUser(user_id="admin", email=None, provider="admin")
    return await _ensure_user_profile(admin_user)


async def require_admin_user(user: AuthenticatedUser = Depends(require_auth)) -> AuthenticatedUser:
    if not is_admin_email(user.email):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# Backward compatibility alias
require_admin = require_admin_user


async def get_db_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session
