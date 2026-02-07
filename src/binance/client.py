import asyncio
import hashlib
import hmac
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from binance.protocols import BinanceMarketDataClient, BinanceTradingClient


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
        self._time_offset: int = 0
        self._last_time_sync: float = 0.0
        self._time_sync_interval: float = 300.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_server_time(self) -> dict[str, Any]:
        response = await self._client.get("/fapi/v1/time")
        response.raise_for_status()
        return response.json()

    async def sync_time(self) -> int:
        """Binance 서버와의 시간 차이(ms)를 계산하고 저장.
        
        Returns:
            서버와의 시간 차이 (밀리초)
        """
        try:
            local_before = int(time.time() * 1000)
            server_data = await self.fetch_server_time()
            local_after = int(time.time() * 1000)
            
            server_time = server_data["serverTime"]
            local_time = (local_before + local_after) // 2
            
            self._time_offset = server_time - local_time
            self._last_time_sync = time.time()
            
            if abs(self._time_offset) > 1000:
                print(f"⚠️ 서버 시간 동기화: offset={self._time_offset}ms (로컬 시간과 {abs(self._time_offset)/1000:.1f}초 차이)")
            
            return self._time_offset
        except Exception as e:
            print(f"⚠️ 서버 시간 동기화 실패: {e}")
            return self._time_offset

    def _get_adjusted_timestamp(self) -> int:
        """보정된 타임스탬프 반환 (서버 시간 기준).
        
        Returns:
            서버 시간에 맞춰 보정된 밀리초 타임스탬프
        """
        return int(time.time() * 1000) + self._time_offset

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

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self._client.get("/fapi/v1/klines", params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # 2초, 4초, 6초 대기
                    print(f"⚠️ 네트워크 타임아웃 발생. {wait_time}초 후 재시도... (시도 {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise ValueError(
                        f"Binance API timeout: {symbol} {interval} | {type(e).__name__}"
                    ) from e

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
            "newOrderRespType": "RESULT",  # 최종 FILLED 결과 직접 반환
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

    async def fetch_account_info(self) -> dict[str, Any]:
        """선물 계정 정보 조회 (/fapi/v2/account)."""
        response = await self._signed_request("GET", "/fapi/v2/account", {})
        if not isinstance(response, dict):
            raise ValueError("Unexpected response type from /fapi/v2/account")
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

    async def fetch_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """주문 정보 조회.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)
            order_id: 주문 ID

        Returns:
            주문 정보 (status, executedQty, origQty 등 포함)
        """
        payload: dict[str, Any] = {"symbol": symbol, "orderId": order_id}
        response = await self._signed_request("GET", "/fapi/v1/order", payload)
        return response

    async def create_listen_key(self) -> str:
        """유저데이터 스트림용 listenKey 생성."""
        response = await self._client.post("/fapi/v1/listenKey")
        response.raise_for_status()
        data = response.json()
        listen_key = data.get("listenKey") if isinstance(data, dict) else None
        if not listen_key:
            raise ValueError("Failed to create listenKey")
        return listen_key

    async def keepalive_listen_key(self, listen_key: str) -> None:
        """유저데이터 스트림 listenKey 갱신."""
        response = await self._client.put("/fapi/v1/listenKey", params={"listenKey": listen_key})
        response.raise_for_status()

    async def close_listen_key(self, listen_key: str) -> None:
        """유저데이터 스트림 listenKey 종료."""
        response = await self._client.delete("/fapi/v1/listenKey", params={"listenKey": listen_key})
        response.raise_for_status()

    async def fetch_user_trades(
        self,
        symbol: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """사용자 거래 내역 조회.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)
            start_time: 시작 시간 (밀리초 타임스탬프)
            end_time: 종료 시간 (밀리초 타임스탬프)
            limit: 조회 개수 (최대 1000)

        Returns:
            거래 내역 목록
            [
                {
                    "symbol": "BTCUSDT",
                    "id": 12345,
                    "orderId": 11579702518,
                    "side": "SELL",
                    "price": "90579.20",
                    "qty": "0.225",
                    "realizedPnl": "0.00",
                    "marginAsset": "USDT",
                    "quoteQty": "20380.32",
                    "commission": "8.152",
                    "commissionAsset": "USDT",
                    "time": 1736579227000,
                    "positionSide": "BOTH",
                    "buyer": false,
                    "maker": false
                },
                ...
            ]
        """
        payload: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start_time is not None:
            payload["startTime"] = start_time
        if end_time is not None:
            payload["endTime"] = end_time
        response = await self._signed_request("GET", "/fapi/v1/userTrades", payload)
        return response if isinstance(response, list) else []

    async def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> dict:
        max_retries = 5
        base_delay = 1.0
        
        for attempt in range(max_retries):
            params_with_sig = self._attach_signature(dict(params))
            try:
                response = await self._client.request(method, path, params=params_with_sig)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                try:
                    data = e.response.json()
                except Exception:  # noqa: BLE001
                    data = {"raw": e.response.text}
                
                error_code = data.get("code") if isinstance(data, dict) else None
                
                if error_code == -1021:
                    if attempt < max_retries - 1:
                        await self.sync_time()
                        delay = base_delay * (2 ** attempt)
                        print(f"⚠️ Timestamp 에러 감지. 시간 동기화 후 {delay:.1f}초 대기... (시도 {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                
                if e.response.status_code == 418:
                    error_msg = data.get("msg", "") if isinstance(data, dict) else str(data)
                    banned_until_ts = self._extract_banned_until_timestamp(error_msg)
                    
                    if banned_until_ts:
                        current_ts = self._get_adjusted_timestamp()
                        wait_time_ms = max(0, banned_until_ts - current_ts)
                        wait_time_sec = wait_time_ms / 1000.0
                        
                        if wait_time_sec > 0 and attempt < max_retries - 1:
                            actual_wait = min(wait_time_sec + 1.0, 120.0)
                            print(f"⚠️ IP 차단 감지. {actual_wait:.1f}초 대기 후 재시도... (시도 {attempt + 1}/{max_retries})")
                            await asyncio.sleep(actual_wait)
                            continue
                        else:
                            error_msg_full = f"IP banned until {datetime.fromtimestamp(banned_until_ts / 1000).isoformat()}"
                            raise ValueError(
                                f"Binance API error: {e.response.status_code} {method} {path} | {error_msg_full}"
                            ) from e
                
                if e.response.status_code == 429 or error_code == -1003:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) * 2
                        print(f"⚠️ Rate Limit 초과. {delay:.1f}초 대기 후 재시도... (시도 {attempt + 1}/{max_retries})")
                        await asyncio.sleep(min(delay, 60.0))
                        continue
                
                raise ValueError(
                    f"Binance API error: {e.response.status_code} {method} {path} | payload={data}"
                ) from e
        
        raise ValueError(f"Binance API error: max retries exceeded for {method} {path}")

    @staticmethod
    def _extract_banned_until_timestamp(error_msg: str) -> int | None:
        """에러 메시지에서 'banned until <timestamp>' 형식의 타임스탬프를 추출.
        
        Args:
            error_msg: 에러 메시지 문자열
            
        Returns:
            밀리초 단위 타임스탬프 또는 None
        """
        # "banned until 1767288777555" 형식의 패턴 매칭
        pattern = r"banned until (\d+)"
        match = re.search(pattern, error_msg, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                pass
        return None

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
        params.setdefault("timestamp", self._get_adjusted_timestamp())
        params.setdefault("recvWindow", 60000)
        query_string = urlencode(params, doseq=True)
        signature = hmac.new(self._api_secret, query_string.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return params
