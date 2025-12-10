from collections.abc import AsyncGenerator

from fastapi import Depends

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import Settings, get_settings


async def provide_binance_client(
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[BinanceHTTPClient, None]:
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )
    try:
        yield client
    finally:
        await client.aclose()


def get_binance_client(
    client: BinanceHTTPClient = Depends(provide_binance_client),
) -> BinanceHTTPClient:
    return client




