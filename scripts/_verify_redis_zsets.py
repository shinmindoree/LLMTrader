"""Quick redis ZSET inspection for live MFP indicators.

Usage (run inside any Container App that has Redis MI access):
    uv run python scripts/_verify_redis_zsets.py
"""

from __future__ import annotations

import os

from azure.identity import ManagedIdentityCredential
import redis


def _client() -> redis.Redis:
    host = os.environ["REDIS_HOST"]
    port = int(os.environ.get("REDIS_PORT", "6380"))
    user = os.environ["REDIS_USERNAME"]
    cred = ManagedIdentityCredential()
    token = cred.get_token("https://redis.azure.com/.default").token
    return redis.Redis(
        host=host,
        port=port,
        ssl=True,
        username=user,
        password=token,
        decode_responses=True,
    )


def main() -> None:
    r = _client()
    for k in [
        "oi:BTCUSDT:hist",
        "funding:BTCUSDT:hist",
        "taker:BTCUSDT:hist",
        "lsr:BTCUSDT:hist",
    ]:
        n = r.zcard(k)
        last = r.zrange(k, -1, -1, withscores=True)
        first = r.zrange(k, 0, 0, withscores=True)
        print(f"{k}: card={n}")
        print(f"  first={first}")
        print(f"  last ={last}")


if __name__ == "__main__":
    main()
