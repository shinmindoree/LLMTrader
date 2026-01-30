"""Optional API key authentication for relay (caller = SaaS backend)."""

from __future__ import annotations

from fastapi import Header, HTTPException

from relay.config import get_config


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    """Validate X-API-Key or Authorization: Bearer. No-op if RELAY_API_KEY is not set."""
    config = get_config()
    if not config.is_api_key_required():
        return
    token = x_api_key
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token or token != config.relay_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
