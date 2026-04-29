from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

REDIS_AAD_SCOPE = "https://redis.azure.com/.default"


def redis_connection_kwargs(
    redis_url: str,
    *,
    decode_responses: bool,
    socket_connect_timeout: int,
    socket_timeout: int,
) -> dict[str, Any]:
    parsed = urlparse(redis_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ValueError("REDIS_URL must use redis:// or rediss:// with a host")

    db = 0
    path = parsed.path.strip("/")
    if path:
        db = int(path.split("/", 1)[0])

    kwargs: dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or (6380 if parsed.scheme == "rediss" else 6379),
        "db": db,
        "password": unquote(parsed.password) if parsed.password is not None else None,
        "decode_responses": decode_responses,
        "socket_connect_timeout": socket_connect_timeout,
        "socket_timeout": socket_timeout,
    }

    username = unquote(parsed.username) if parsed.username else None
    if username:
        kwargs["username"] = username
    if parsed.scheme == "rediss":
        kwargs["ssl"] = True
    return kwargs


def create_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = True,
    socket_connect_timeout: int = 5,
    socket_timeout: int = 5,
) -> Any:
    import redis

    return redis.Redis(
        **redis_connection_kwargs(
            redis_url,
            decode_responses=decode_responses,
            socket_connect_timeout=socket_connect_timeout,
            socket_timeout=socket_timeout,
        )
    )


def create_redis_client_from_parts(
    *,
    host: str,
    port: int = 6380,
    password: str,
    ssl: bool = True,
    db: int = 0,
    decode_responses: bool = True,
    socket_connect_timeout: int = 5,
    socket_timeout: int = 5,
) -> Any:
    import redis

    return redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        ssl=ssl,
        decode_responses=decode_responses,
        socket_connect_timeout=socket_connect_timeout,
        socket_timeout=socket_timeout,
    )


class AzureRedisCredentialProvider:
    def __init__(self, username: str) -> None:
        from azure.identity import DefaultAzureCredential

        self._username = username
        self._credential = DefaultAzureCredential()
        self._token = ""
        self._expires_on = 0

    def get_credentials(self) -> tuple[str, str]:
        import time

        if not self._token or time.time() > self._expires_on - 300:
            token = self._credential.get_token(REDIS_AAD_SCOPE)
            self._token = token.token
            self._expires_on = int(token.expires_on)
        return self._username, self._token


def create_redis_client_with_aad(
    *,
    host: str,
    username: str,
    port: int = 6380,
    ssl: bool = True,
    db: int = 0,
    decode_responses: bool = True,
    socket_connect_timeout: int = 5,
    socket_timeout: int = 5,
) -> Any:
    import redis

    return redis.Redis(
        host=host,
        port=port,
        db=db,
        ssl=ssl,
        credential_provider=AzureRedisCredentialProvider(username),
        decode_responses=decode_responses,
        socket_connect_timeout=socket_connect_timeout,
        socket_timeout=socket_timeout,
    )


def create_async_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = True,
    socket_connect_timeout: int = 5,
    socket_timeout: int = 5,
) -> Any:
    import redis.asyncio as aioredis

    return aioredis.Redis(
        **redis_connection_kwargs(
            redis_url,
            decode_responses=decode_responses,
            socket_connect_timeout=socket_connect_timeout,
            socket_timeout=socket_timeout,
        )
    )