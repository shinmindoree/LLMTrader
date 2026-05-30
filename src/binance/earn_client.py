"""Binance Spot REST client for Simple Earn (Flexible) + Futures Universal Transfer.

Mainnet-only client used by the Auto-Sweep engine to:
  - Read Spot USDT balance
  - Read Futures available balance
  - Subscribe/redeem USDT Flexible product
  - Transfer USDT between Futures ↔ Spot wallets (Universal Transfer)

Kept separate from BinanceHTTPClient because that client targets fapi.binance.com
whereas Simple Earn and Universal Transfer live on api.binance.com (sapi).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

_log = logging.getLogger("llmtrader.binance.earn")

SPOT_MAINNET_BASE = "https://api.binance.com"
FUTURES_MAINNET_BASE = "https://fapi.binance.com"


class BinanceEarnClientError(RuntimeError):
    pass


class BinanceEarnClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = SPOT_MAINNET_BASE,
        futures_base_url: str = FUTURES_MAINNET_BASE,
        timeout: float = 10.0,
    ) -> None:
        if not api_key or not api_secret:
            raise BinanceEarnClientError("api_key and api_secret required")
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"X-MBX-APIKEY": api_key},
        )
        self._futures_client = httpx.AsyncClient(
            base_url=futures_base_url,
            timeout=timeout,
            headers={"X-MBX-APIKEY": api_key},
        )
        self._time_offset_ms: int = 0
        self._last_sync_ts: float = 0.0
        self._sync_interval_sec: float = 300.0
        self._cached_product_id: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._futures_client.aclose()

    # ── time sync ──────────────────────────────────────────

    async def _maybe_sync_time(self) -> None:
        if time.time() - self._last_sync_ts < self._sync_interval_sec:
            return
        try:
            before = int(time.time() * 1000)
            r = await self._client.get("/api/v3/time")
            r.raise_for_status()
            after = int(time.time() * 1000)
            server_ts = int(r.json()["serverTime"])
            self._time_offset_ms = server_ts - (before + after) // 2
            self._last_sync_ts = time.time()
        except Exception as exc:  # noqa: BLE001
            _log.warning("Earn client time sync failed: %s", exc)

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        params.setdefault("timestamp", self._timestamp())
        params.setdefault("recvWindow", 60000)
        qs = urlencode(params, doseq=True)
        sig = hmac.new(self._api_secret, qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    async def _signed(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._maybe_sync_time()
        signed = self._sign(dict(params or {}))
        r = await self._client.request(method, path, params=signed)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text}
            raise BinanceEarnClientError(f"{method} {path} -> {r.status_code}: {data}")
        return r.json()

    async def _signed_futures(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._maybe_sync_time()
        signed = self._sign(dict(params or {}))
        r = await self._futures_client.request(method, path, params=signed)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text}
            raise BinanceEarnClientError(f"{method} {path} -> {r.status_code}: {data}")
        return r.json()

    # ── Spot balance ───────────────────────────────────────

    async def fetch_spot_usdt_balance(self) -> float:
        data = await self._signed("GET", "/api/v3/account")
        for bal in data.get("balances", []):
            if bal.get("asset") == "USDT":
                return float(bal.get("free", 0.0))
        return 0.0

    # ── Futures balance ────────────────────────────────────

    async def fetch_futures_available_balance(self) -> float:
        """Return USDT available balance in the USD-M Futures wallet."""
        data = await self._signed_futures("GET", "/fapi/v2/balance")
        for item in (data if isinstance(data, list) else []):
            if item.get("asset") == "USDT":
                return float(item.get("availableBalance", 0.0))
        return 0.0

    # ── Universal Transfer (Futures ↔ Spot) ───────────────

    async def transfer_futures_to_spot(self, amount: float) -> dict[str, Any]:
        """Move USDT from USD-M Futures wallet to Spot wallet."""
        payload = {"type": "UMFUTURE_MAIN", "asset": "USDT", "amount": f"{amount:.2f}"}
        return await self._signed("POST", "/sapi/v1/asset/transfer", payload)

    async def transfer_spot_to_futures(self, amount: float) -> dict[str, Any]:
        """Move USDT from Spot wallet to USD-M Futures wallet."""
        payload = {"type": "MAIN_UMFUTURE", "asset": "USDT", "amount": f"{amount:.2f}"}
        return await self._signed("POST", "/sapi/v1/asset/transfer", payload)

    # ── Simple Earn ────────────────────────────────────────

    async def get_usdt_flexible_product_id(self) -> str | None:
        if self._cached_product_id:
            return self._cached_product_id
        data = await self._signed(
            "GET", "/sapi/v1/simple-earn/flexible/list", {"asset": "USDT", "size": 100}
        )
        rows = data.get("rows") or []
        for row in rows:
            if row.get("asset") == "USDT" and row.get("canPurchase", True):
                pid = str(row.get("productId") or "")
                if pid:
                    self._cached_product_id = pid
                    return pid
        return None

    async def fetch_flexible_position_usdt(self) -> float:
        data = await self._signed(
            "GET", "/sapi/v1/simple-earn/flexible/position", {"asset": "USDT"}
        )
        rows = data.get("rows") or []
        total = 0.0
        for row in rows:
            if row.get("asset") == "USDT":
                total += float(row.get("totalAmount", 0.0))
        return total

    async def subscribe(self, amount: float, product_id: str) -> dict[str, Any]:
        payload = {"productId": product_id, "amount": f"{amount:.2f}"}
        return await self._signed("POST", "/sapi/v1/simple-earn/flexible/subscribe", payload)

    async def redeem(self, amount: float, product_id: str) -> dict[str, Any]:
        payload = {"productId": product_id, "amount": f"{amount:.2f}"}
        return await self._signed("POST", "/sapi/v1/simple-earn/flexible/redeem", payload)
