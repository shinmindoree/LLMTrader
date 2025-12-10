import pytest
import respx
from httpx import Response

from llmtrader.binance.client import BinanceHTTPClient


@pytest.mark.asyncio
@respx.mock
async def test_fetch_klines() -> None:
    route = respx.get("https://testnet.binancefuture.com/fapi/v1/klines").mock(
        return_value=Response(200, json=[[1, 2, 3]])
    )

    client = BinanceHTTPClient(
        api_key="",
        api_secret="dummy",
        base_url="https://testnet.binancefuture.com",
    )
    data = await client.fetch_klines("BTCUSDT", "1m", limit=1)
    await client.aclose()

    assert route.called
    assert data == [[1, 2, 3]]


@pytest.mark.asyncio
@respx.mock
async def test_signed_order_request() -> None:
    respx.post("https://testnet.binancefuture.com/fapi/v1/order").mock(
        return_value=Response(200, json={"orderId": 123})
    )

    client = BinanceHTTPClient(
        api_key="key",
        api_secret="secret",
        base_url="https://testnet.binancefuture.com",
    )

    result = await client.place_order("BTCUSDT", "BUY", 0.001, type="MARKET")
    await client.aclose()

    assert result["orderId"] == 123




