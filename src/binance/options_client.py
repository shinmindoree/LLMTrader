"""Binance European Options REST 클라이언트 (Phase 0 PoC).

Binance Options API (``eapi``) 의 **읽기 전용** 엔드포인트를 우선 지원한다.
주문(``POST /eapi/v1/order``) 등 트레이딩 엔드포인트는 Phase 1 에서 추가한다.

베이스 URL:
    - 메인넷: ``https://eapi.binance.com``
    - 테스트넷: ``https://testnet.binancefuture.com``
      (퓨처스 테스트넷과 동일 호스트에서 ``/eapi/v1/*`` 경로로 옵션 데이터 제공)

서명 규칙은 ``BinanceHTTPClient`` 와 동일하다 (HMAC-SHA256 over urlencoded
query string, ``X-MBX-APIKEY`` 헤더). 본 PoC 단계에서는 공개 엔드포인트만
사용하므로 키가 없어도 동작한다.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from binance.client import normalize_binance_base_url

OPTIONS_MAINNET_BASE: str = "https://eapi.binance.com"
OPTIONS_TESTNET_BASE: str = "https://testnet.binancefuture.com"


def resolve_options_base_url(env: str | None) -> str:
    """``env`` 키워드(``"mainnet"`` / ``"testnet"``)를 옵션 베이스 URL로 매핑."""
    key = (env or "testnet").strip().lower()
    if key in {"mainnet", "live", "prod", "production"}:
        return OPTIONS_MAINNET_BASE
    if key in {"testnet", "test", "demo"}:
        return OPTIONS_TESTNET_BASE
    return normalize_binance_base_url(env, fallback=OPTIONS_TESTNET_BASE)


class BinanceOptionsClientError(RuntimeError):
    """옵션 API 호출 실패 시 raise 되는 일반 예외."""


class BinanceOptionsClient:
    """Binance European Options REST 클라이언트 (read-only PoC).

    동일 패키지의 ``BinanceHTTPClient`` 와 같은 어휘(``aclose``, 서명 헬퍼)를
    따른다. 트레이딩 메서드는 의도적으로 누락되어 있으며, Phase 1에서
    추가 예정이다.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        api_secret: str = "",
        base_url: str | None = None,
        env: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        """클라이언트 초기화.

        Args:
            api_key: API 키. 공개 엔드포인트만 사용한다면 빈 문자열로 둬도 된다.
            api_secret: API 시크릿. 서명 호출 시 필요.
            base_url: 직접 베이스 URL을 지정. 우선순위가 ``env`` 보다 높다.
            env: ``"mainnet"`` 또는 ``"testnet"``. ``base_url`` 미지정 시 사용.
            timeout: HTTP 타임아웃(초).
        """
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        resolved = base_url if base_url else resolve_options_base_url(env)
        self.base_url = normalize_binance_base_url(resolved, fallback=OPTIONS_TESTNET_BASE)
        headers: dict[str, str] | None = (
            {"X-MBX-APIKEY": api_key} if api_key else None
        )
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── public market data ────────────────────────────────────────────

    async def ping(self) -> dict[str, Any]:
        """``GET /eapi/v1/ping`` — 연결 확인."""
        data = await self._public_get("/eapi/v1/ping")
        return data if isinstance(data, dict) else {}

    async def fetch_server_time(self) -> int:
        """``GET /eapi/v1/time`` — 서버 시간 (ms)."""
        data = await self._public_get("/eapi/v1/time")
        if not isinstance(data, dict) or "serverTime" not in data:
            raise BinanceOptionsClientError(f"Unexpected /time response: {data!r}")
        return int(data["serverTime"])

    async def fetch_exchange_info(self) -> dict[str, Any]:
        """``GET /eapi/v1/exchangeInfo`` — 옵션 심볼 메타데이터.

        반환 본문에는 ``optionSymbols`` (전체 옵션 리스트), ``optionContracts``
        (기초자산별 컨트랙트 정보), ``rateLimits`` 등이 포함된다.
        """
        data = await self._public_get("/eapi/v1/exchangeInfo")
        if not isinstance(data, dict):
            raise BinanceOptionsClientError(
                f"Unexpected /exchangeInfo response type: {type(data).__name__}"
            )
        return data

    async def fetch_index_price(self, underlying: str) -> float:
        """``GET /eapi/v1/index`` — 기초자산 인덱스 가격.

        Args:
            underlying: ``"BTCUSDT"`` 처럼 견적 자산이 붙은 표기.
        """
        data = await self._public_get(
            "/eapi/v1/index", params={"underlying": underlying.upper()}
        )
        if not isinstance(data, dict) or "indexPrice" not in data:
            raise BinanceOptionsClientError(f"Unexpected /index response: {data!r}")
        return float(data["indexPrice"])

    async def fetch_mark(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """``GET /eapi/v1/mark`` — 옵션 마크 가격 + Greeks + IV.

        Args:
            symbol: 특정 옵션 심볼만 조회. ``None`` 이면 전체.

        Returns:
            ``[{symbol, markPrice, bidIV, askIV, markIV, delta, gamma, vega,
            theta, highPriceLimit, lowPriceLimit, riskFreeInterest}, ...]``
        """
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = await self._public_get("/eapi/v1/mark", params=params or None)
        if not isinstance(data, list):
            raise BinanceOptionsClientError(
                f"Unexpected /mark response type: {type(data).__name__}"
            )
        return data

    async def fetch_ticker(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """``GET /eapi/v1/ticker`` — 24시간 통계.

        Args:
            symbol: 특정 심볼만 조회. ``None`` 이면 전체.
        """
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = await self._public_get("/eapi/v1/ticker", params=params or None)
        if not isinstance(data, list):
            raise BinanceOptionsClientError(
                f"Unexpected /ticker response type: {type(data).__name__}"
            )
        return data

    async def fetch_depth(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        """``GET /eapi/v1/depth`` — 호가창 스냅샷.

        Args:
            symbol: 옵션 심볼.
            limit: 호가 깊이 (Binance 허용 값: 5, 10, 20, 50, 100, 500, 1000).
        """
        data = await self._public_get(
            "/eapi/v1/depth", params={"symbol": symbol.upper(), "limit": int(limit)}
        )
        if not isinstance(data, dict):
            raise BinanceOptionsClientError(
                f"Unexpected /depth response type: {type(data).__name__}"
            )
        return data

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """``GET /eapi/v1/klines`` — 옵션 캔들.

        Args:
            symbol: 옵션 심볼.
            interval: ``1m``, ``5m``, ``1h`` 등. 옵션은 데이터가 얕아 ``1h+`` 권장.
            start_ts/end_ts: 밀리초 범위.
            limit: 최대 1500.
        """
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": int(limit),
        }
        if start_ts is not None:
            params["startTime"] = int(start_ts)
        if end_ts is not None:
            params["endTime"] = int(end_ts)
        data = await self._public_get("/eapi/v1/klines", params=params)
        if not isinstance(data, list):
            raise BinanceOptionsClientError(
                f"Unexpected /klines response type: {type(data).__name__}"
            )
        return data

    async def fetch_historical_trades(
        self, symbol: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """``GET /eapi/v1/historicalTrades`` — 과거 체결 내역."""
        data = await self._public_get(
            "/eapi/v1/historicalTrades",
            params={"symbol": symbol.upper(), "limit": int(limit)},
        )
        if not isinstance(data, list):
            raise BinanceOptionsClientError(
                f"Unexpected /historicalTrades response type: {type(data).__name__}"
            )
        return data

    async def fetch_exercise_history(
        self,
        *,
        underlying: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """``GET /eapi/v1/exerciseHistory`` — 만기 정산(행사) 이력."""
        params: dict[str, Any] = {"limit": int(limit)}
        if underlying:
            params["underlying"] = underlying.upper()
        if start_ts is not None:
            params["startTime"] = int(start_ts)
        if end_ts is not None:
            params["endTime"] = int(end_ts)
        data = await self._public_get("/eapi/v1/exerciseHistory", params=params)
        if not isinstance(data, list):
            raise BinanceOptionsClientError(
                f"Unexpected /exerciseHistory response type: {type(data).__name__}"
            )
        return data

    # ── internal HTTP helpers ─────────────────────────────────────────

    async def _public_get(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text.strip()
            preview = body[:300] + ("..." if len(body) > 300 else "")
            raise BinanceOptionsClientError(
                f"Options API error: {exc.response.status_code} GET {path} | "
                f"params={params} | body={preview}"
            ) from exc
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            raise BinanceOptionsClientError(
                f"Options API timeout: GET {path} | {type(exc).__name__}"
            ) from exc

    def _attach_signature(self, params: dict[str, Any]) -> dict[str, Any]:
        """Phase 1 트레이딩 메서드에서 사용할 서명 헬퍼.

        현재 PoC 단계의 read-only 메서드는 이 헬퍼를 호출하지 않는다.
        """
        if not self._api_secret:
            raise BinanceOptionsClientError("api_secret required for signed requests")
        signed = dict(params)
        signed.setdefault("timestamp", int(time.time() * 1000))
        signed.setdefault("recvWindow", 60000)
        query = urlencode(signed, doseq=True)
        signature = hmac.new(self._api_secret, query.encode(), hashlib.sha256).hexdigest()
        signed["signature"] = signature
        return signed
