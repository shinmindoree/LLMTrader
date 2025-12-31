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
        self.base_url = base_url
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

    async def fetch_exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        """거래소 정보 조회 (심볼별 필터 정보 포함).

        Args:
            symbol: 특정 심볼만 조회 (None이면 전체)

        Returns:
            심볼별 필터 정보를 파싱한 딕셔너리:
            {
                "BTCUSDT": {
                    "step_size": "0.001",      # LOT_SIZE - 수량 스텝
                    "tick_size": "0.10",       # PRICE_FILTER - 가격 스텝
                    "min_notional": "5.0",     # MIN_NOTIONAL - 최소 주문 금액
                    "min_qty": "0.001",        # LOT_SIZE - 최소 수량
                    "max_qty": "1000.0",       # LOT_SIZE - 최대 수량
                },
                ...
            }
        """
        response = await self._client.get("/fapi/v1/exchangeInfo")
        response.raise_for_status()
        data = response.json()

        result: dict[str, Any] = {}
        for sym_info in data.get("symbols", []):
            sym = sym_info.get("symbol")
            if symbol and sym != symbol:
                continue

            filters = {f["filterType"]: f for f in sym_info.get("filters", [])}
            parsed: dict[str, Any] = {}

            # LOT_SIZE: 수량 정밀도
            lot_size = filters.get("LOT_SIZE", {})
            parsed["step_size"] = lot_size.get("stepSize", "0.001")
            parsed["min_qty"] = lot_size.get("minQty", "0.001")
            parsed["max_qty"] = lot_size.get("maxQty", "1000")

            # PRICE_FILTER: 가격 정밀도
            price_filter = filters.get("PRICE_FILTER", {})
            parsed["tick_size"] = price_filter.get("tickSize", "0.01")

            # MIN_NOTIONAL: 최소 주문 금액
            min_notional = filters.get("MIN_NOTIONAL", {})
            parsed["min_notional"] = min_notional.get("notional", "5.0")

            result[sym] = parsed

            if symbol:
                break

        return result

    async def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """미체결 주문 목록 조회.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)

        Returns:
            미체결 주문 목록
        """
        payload: dict[str, Any] = {"symbol": symbol}
        response = await self._signed_request("GET", "/fapi/v1/openOrders", payload)
        return response if isinstance(response, list) else []

    async def fetch_commission_rate(self, symbol: str) -> dict[str, Any]:
        """사용자 수수료율 조회.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)

        Returns:
            {
                "symbol": "BTCUSDT",
                "makerCommissionRate": "0.0002",  # 0.02%
                "takerCommissionRate": "0.0004",  # 0.04%
                "rpiCommissionRate": "0.00005"   # 0.005%
            }
        """
        payload: dict[str, Any] = {"symbol": symbol}
        response = await self._signed_request("GET", "/fapi/v1/commissionRate", payload)
        return response

    async def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> dict:
        params_with_sig = self._attach_signature(params)
        try:
            response = await self._client.request(method, path, params=params_with_sig)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # Binance는 에러 바디에 {"code": ..., "msg": "..."} 형태를 주는 경우가 많음.
            # 기존 로그만으로는 원인을 확정하기 어려워서, 응답 바디를 포함해 예외 메시지를 강화한다.
            try:
                data = e.response.json()
            except Exception:  # noqa: BLE001
                data = {"raw": e.response.text}
            raise ValueError(
                f"Binance API error: {e.response.status_code} {method} {path} | payload={data}"
            ) from e

    @staticmethod
    def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
        """서명/요청에 사용할 파라미터를 정규화한다.

        중요:
        - httpx는 bool 값을 쿼리스트링에서 'true'/'false'로 직렬화하는데,
          urllib.parse.urlencode는 Python bool을 'True'/'False'로 바꿉니다.
          이 불일치가 생기면 signature가 틀어져 4xx가 발생할 수 있습니다.
        - 따라서 bool은 명시적으로 소문자 문자열로 변환합니다.
        """
        normalized: dict[str, Any] = {}
        for k, v in params.items():
            if isinstance(v, bool):
                normalized[k] = "true" if v else "false"
            elif isinstance(v, float):
                # float 노이즈(예: 0.013000000000000001)로 인한 precision 에러 방지:
                # 고정소수 문자열로 변환 후 불필요한 0 제거
                s = f"{v:.15f}".rstrip("0").rstrip(".")
                normalized[k] = s if s else "0"
            elif isinstance(v, (list, tuple)):
                items: list[Any] = []
                for item in v:
                    if isinstance(item, bool):
                        items.append("true" if item else "false")
                    elif isinstance(item, float):
                        s = f"{item:.15f}".rstrip("0").rstrip(".")
                        items.append(s if s else "0")
                    else:
                        items.append(item)
                normalized[k] = items
            else:
                normalized[k] = v
        return normalized

    def _attach_signature(self, params: dict[str, Any]) -> dict[str, Any]:
        params = self._normalize_params(dict(params))
        params.setdefault("timestamp", int(time.time() * 1000))
        query_string = urlencode(params, doseq=True)
        signature = hmac.new(self._api_secret, query_string.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return params

