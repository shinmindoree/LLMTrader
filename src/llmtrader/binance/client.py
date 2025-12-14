import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from llmtrader.binance.protocols import BinanceMarketDataClient, BinanceTradingClient


class BinanceHTTPClient(BinanceMarketDataClient, BinanceTradingClient):
    """바이낸스 선물 REST 클라이언트 (테스트넷/실서버 공용)."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"X-MBX-APIKEY": api_key} if api_key else None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_server_time(self) -> dict[str, Any]:
        response = await self._client.get("/fapi/v1/time")
        response.raise_for_status()
        return response.json()

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ts is not None:
            params["startTime"] = start_ts
        if end_ts is not None:
            params["endTime"] = end_ts

        response = await self._client.get("/fapi/v1/klines", params=params)
        response.raise_for_status()
        return response.json()

    async def fetch_ticker_price(self, symbol: str) -> float:
        """최신 체결가(Last Price) 조회.

        참고: 바이낸스 UI에서 보는 가격(Last/Mark/Index) 중 무엇을 기준으로 하느냐에 따라
        값이 다를 수 있습니다. 이 메서드는 /fapi/v1/ticker/price 의 price(Last Price)를 반환합니다.
        """
        response = await self._client.get("/fapi/v1/ticker/price", params={"symbol": symbol})
        response.raise_for_status()
        data = response.json()
        return float(data["price"])

    async def fetch_mark_price(self, symbol: str) -> float:
        """마크 가격(Mark Price) 조회."""
        response = await self._client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
        response.raise_for_status()
        data = response.json()
        return float(data["markPrice"])

    async def place_order(self, symbol: str, side: str, quantity: float, **params: object) -> dict:
        payload: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": params.pop("type", "MARKET"),
            "quantity": quantity,
        }
        # None/빈값 필터링
        filtered_params = {k: v for k, v in params.items() if v is not None and v != ""}
        payload.update(filtered_params)
        response = await self._signed_request("POST", "/fapi/v1/order", payload)
        return response

    async def cancel_order(self, symbol: str, order_id: int) -> dict:
        payload: dict[str, Any] = {"symbol": symbol, "orderId": order_id}
        response = await self._signed_request("DELETE", "/fapi/v1/order", payload)
        return response

    async def fetch_position(self, symbol: str) -> dict:
        payload: dict[str, Any] = {"symbol": symbol}
        response = await self._signed_request("GET", "/fapi/v2/positionRisk", payload)
        return response

    async def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> dict:
        params_with_sig = self._attach_signature(params)
        response = await self._client.request(method, path, params=params_with_sig)
        response.raise_for_status()
        return response.json()

    def _attach_signature(self, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params)
        params.setdefault("timestamp", int(time.time() * 1000))
        query_string = urlencode(params, doseq=True)
        signature = hmac.new(self._api_secret, query_string.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return params

