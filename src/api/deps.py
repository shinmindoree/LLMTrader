from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncio

import jwt
from fastapi import Depends, Header, HTTPException

logger = logging.getLogger(__name__)
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from settings import get_settings

_session_maker_ref: async_sessionmaker[AsyncSession] | None = None
_jwk_client: PyJWKClient | None = None

# In-memory cache for user profiles: email -> (user_id, plan, timestamp)
_PROFILE_CACHE: dict[str, tuple[str, str, float]] = {}
_PROFILE_CACHE_TTL = 300  # 5 minutes
_REDIS_PROFILE_TTL = 1800  # 30 minutes (shared across replicas)
_REDIS_PROFILE_PREFIX = "profile:"


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


async def _redis_get_profile(norm_email: str) -> tuple[str, str] | None:
    """L2 cache: try to read profile from Redis. Returns (user_id, plan) or None."""
    try:
        from api.kline_cache import _get_redis

        r = await _get_redis()
        if r is None:
            return None
        import json as _json

        data = await r.get(f"{_REDIS_PROFILE_PREFIX}{norm_email}")
        if data is None:
            return None
        parsed = _json.loads(data)
        return (parsed["user_id"], parsed["plan"])
    except Exception:  # noqa: BLE001
        logger.debug("Redis profile cache read error", exc_info=True)
        return None


async def _redis_set_profile(norm_email: str, user_id: str, plan: str) -> None:
    """L2 cache: write profile to Redis."""
    try:
        from api.kline_cache import _get_redis

        r = await _get_redis()
        if r is None:
            return
        import json as _json

        data = _json.dumps({"user_id": user_id, "plan": plan})
        await r.set(f"{_REDIS_PROFILE_PREFIX}{norm_email}", data.encode(), ex=_REDIS_PROFILE_TTL)
    except Exception:  # noqa: BLE001
        logger.debug("Redis profile cache write error", exc_info=True)


def _set_local_cache(norm_email: str, user_id: str, plan: str) -> None:
    """L1 cache: write to in-memory dict."""
    if norm_email:
        _PROFILE_CACHE[norm_email] = (user_id, plan, time.monotonic())


async def _ensure_user_profile(user: AuthenticatedUser) -> AuthenticatedUser:
    from control.models import UserProfile

    norm_email = _normalize_email(user.email)

    # ── L1: in-memory cache (per-replica, 5min TTL) ──
    if norm_email and norm_email in _PROFILE_CACHE:
        cached_uid, cached_plan, cached_ts = _PROFILE_CACHE[norm_email]
        if time.monotonic() - cached_ts < _PROFILE_CACHE_TTL:
            user.user_id = cached_uid
            user.plan = cached_plan
            return user

    # ── L2: Redis cache (shared across replicas, 30min TTL) ──
    if norm_email:
        redis_hit = await _redis_get_profile(norm_email)
        if redis_hit is not None:
            user.user_id, user.plan = redis_hit
            _set_local_cache(norm_email, user.user_id, user.plan)
            return user

    # ── L3: PostgreSQL (source of truth) ──
    sm = _get_session_maker()
    max_retries = 2
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with sm() as session:
                if norm_email:
                    existing = await session.execute(
                        select(UserProfile)
                        .where(UserProfile.email == norm_email)
                        .order_by(UserProfile.created_at)
                        .limit(1)
                    )
                    existing_profile = existing.scalar_one_or_none()
                    if existing_profile:
                        user.user_id = existing_profile.user_id
                        user.plan = existing_profile.plan or "free"
                        _set_local_cache(norm_email, user.user_id, user.plan)
                        await _redis_set_profile(norm_email, user.user_id, user.plan)
                        return user

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
                _set_local_cache(norm_email, user.user_id, user.plan)
                await _redis_set_profile(norm_email, user.user_id, user.plan)
            return user
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_retries:
                logger.warning("DB error in _ensure_user_profile (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)
                await asyncio.sleep(0.5 * (attempt + 1))
            else:
                logger.error("DB error in _ensure_user_profile after %d attempts: %s", max_retries + 1, exc, exc_info=True)

    # ── L4: Graceful degradation — auth succeeds with plan=free ──
    logger.warning(
        "DB unavailable, using token-only auth for user=%s email=%s (last error: %s)",
        user.user_id, user.email, last_exc,
    )
    user.plan = "free"
    return user


async def _verify_nextauth_user(
    token: str,
    email_header: str | None,
    user_id_header: str | None,
) -> AuthenticatedUser:
    """Verify request from NextAuth web proxy via shared secret."""
    settings = get_settings()
    expected_secret = (settings.nextauth.secret or "").strip()
    if not expected_secret:
        raise HTTPException(status_code=500, detail="AUTH_SECRET is not configured")

    import hmac
    if not hmac.compare_digest(token, expected_secret):
        raise HTTPException(status_code=401, detail="Invalid auth secret")

    email = (email_header or "").strip() or None
    user_id = (user_id_header or "").strip() or (email or "anonymous")
    return AuthenticatedUser(user_id=user_id, email=email, provider="nextauth")


async def require_auth(
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_user_email: str | None = Header(default=None),
    x_chat_user_id: str | None = Header(default=None),
) -> AuthenticatedUser:
    settings = get_settings()

    # 1. Try NextAuth shared-secret auth (preferred)
    nextauth_enabled = settings.nextauth.enabled or bool((settings.nextauth.secret or "").strip())
    if nextauth_enabled:
        token = _extract_bearer_token(authorization)
        if token:
            user = await _verify_nextauth_user(token, x_user_email, x_chat_user_id)
            return await _ensure_user_profile(user)

    # 2. Try Entra ID JWT auth (legacy)
    entra = settings.entra_auth
    entra_enabled = entra.enabled or bool((entra.client_id or "").strip())
    if entra_enabled:
        token = _extract_bearer_token(authorization)
        if token:
            user = await _verify_entra_user(token)
            return await _ensure_user_profile(user)
        if not entra.allow_admin_fallback:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # 3. Fall back to admin token
    expected = settings.admin_token.strip()
    if not expected:
        detail = "ADMIN_TOKEN is not configured"
        if entra_enabled or nextauth_enabled:
            detail = "Auth token is missing and ADMIN fallback is not configured"
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
