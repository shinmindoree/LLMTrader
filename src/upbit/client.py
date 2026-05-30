"""Upbit Open API client.

Handles:
  - JWT authentication (with + without query-string hashing)
  - Account balance queries
  - Deposit address lookup
  - Crypto withdrawals (out)
  - KRW ↔ USDT spot orders (for on/off-ramp conversion)
  - Withdrawal / order status polling
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

_log = logging.getLogger("llmtrader.upbit")

UPBIT_BASE = "https://api.upbit.com"


class UpbitClientError(RuntimeError):
    pass


class UpbitClient:
    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        base_url: str = UPBIT_BASE,
        timeout: float = 15.0,
    ) -> None:
        if not access_key or not secret_key:
            raise UpbitClientError("access_key and secret_key are required")
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── auth ──────────────────────────────────────────────

    def _auth_header(self, query_params: dict[str, Any] | None = None) -> dict[str, str]:
        payload: dict[str, Any] = {
            "access_key": self._access_key,
            "nonce": str(uuid.uuid4()),
        }
        if query_params:
            qs = urlencode(query_params, doseq=True).encode()
            m = hashlib.sha512()
            m.update(qs)
            payload["query_hash"] = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = jwt.encode(payload, self._secret_key)
        return {"Authorization": f"Bearer {token}"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = self._auth_header(params)
        r = await self._client.get(path, params=params, headers=headers)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text}
            raise UpbitClientError(f"GET {path} -> {r.status_code}: {data}")
        return r.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        headers = self._auth_header(body)
        r = await self._client.post(path, json=body, headers=headers)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text}
            raise UpbitClientError(f"POST {path} -> {r.status_code}: {data}")
        return r.json()

    async def _delete(self, path: str, params: dict[str, Any]) -> Any:
        headers = self._auth_header(params)
        r = await self._client.delete(path, params=params, headers=headers)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text}
            raise UpbitClientError(f"DELETE {path} -> {r.status_code}: {data}")
        return r.json()

    # ── accounts ──────────────────────────────────────────

    async def fetch_balances(self) -> list[dict[str, Any]]:
        """Return all account balances.

        Each item: {currency, balance, locked, avg_buy_price, ...}
        """
        return await self._get("/v1/accounts")

    async def fetch_balance(self, currency: str) -> float:
        """Return free balance for a specific currency (e.g. 'KRW', 'USDT')."""
        balances = await self.fetch_balances()
        for item in balances:
            if item.get("currency") == currency:
                return float(item.get("balance", 0.0))
        return 0.0

    # ── deposit address ───────────────────────────────────

    async def get_deposit_address(self, currency: str, net_type: str) -> dict[str, Any]:
        """Get deposit address for a currency + network.

        net_type examples: 'TRC20', 'ERC20', 'BEP20'
        Returns: {currency, deposit_address, secondary_address, net_type, ...}
        """
        params = {"currency": currency, "net_type": net_type}
        return await self._get("/v1/deposits/coin_address", params)

    # ── withdrawals ───────────────────────────────────────

    async def withdraw_crypto(
        self,
        *,
        currency: str,
        amount: float,
        address: str,
        secondary_address: str | None = None,
        net_type: str = "TRC20",
        transaction_type: str = "default",
    ) -> dict[str, Any]:
        """Request a crypto withdrawal.

        Returns: {type, uuid, currency, net_type, txid, state, amount, fee, ...}
        """
        body: dict[str, Any] = {
            "currency": currency,
            "net_type": net_type,
            "amount": str(amount),
            "address": address,
            "transaction_type": transaction_type,
        }
        if secondary_address:
            body["secondary_address"] = secondary_address
        return await self._post("/v1/withdraws/coin", body)

    async def get_withdrawal(self, withdrawal_uuid: str) -> dict[str, Any]:
        """Get withdrawal status by UUID.

        state values: 'submitting', 'submitted', 'almost_accepted',
                      'rejected', 'accepted', 'processing', 'done', 'canceled'
        """
        params = {"uuid": withdrawal_uuid}
        return await self._get("/v1/withdraw", params)

    async def list_withdrawals(
        self,
        *,
        currency: str | None = None,
        state: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if currency:
            params["currency"] = currency
        if state:
            params["state"] = state
        return await self._get("/v1/withdraws", params)

    # ── deposits ──────────────────────────────────────────

    async def get_deposit(self, txid: str) -> dict[str, Any]:
        """Get deposit status by txid."""
        params = {"txid": txid}
        return await self._get("/v1/deposit", params)

    async def list_deposits(
        self,
        *,
        currency: str | None = None,
        state: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if currency:
            params["currency"] = currency
        if state:
            params["state"] = state
        return await self._get("/v1/deposits", params)

    # ── orders ────────────────────────────────────────────

    async def place_market_buy_krw(self, market: str, price: float) -> dict[str, Any]:
        """Place a KRW-denominated market buy order.

        market: e.g. 'KRW-USDT'
        price: KRW amount to spend
        """
        body = {
            "market": market,
            "side": "bid",
            "ord_type": "price",
            "price": str(price),
        }
        return await self._post("/v1/orders", body)

    async def place_market_sell(self, market: str, volume: float) -> dict[str, Any]:
        """Place a market sell order (full volume).

        market: e.g. 'KRW-USDT'
        volume: amount to sell in base currency (USDT)
        """
        body = {
            "market": market,
            "side": "ask",
            "ord_type": "market",
            "volume": str(volume),
        }
        return await self._post("/v1/orders", body)

    async def get_order(self, order_uuid: str) -> dict[str, Any]:
        """Get order status by UUID.

        state: 'wait', 'watch', 'done', 'cancel'
        """
        params = {"uuid": order_uuid}
        return await self._get("/v1/order", params)

    async def cancel_order(self, order_uuid: str) -> dict[str, Any]:
        params = {"uuid": order_uuid}
        return await self._delete("/v1/order", params)

    # ── market data ───────────────────────────────────────

    async def get_ticker(self, markets: list[str]) -> list[dict[str, Any]]:
        """Get current ticker(s). e.g. markets=['KRW-USDT']"""
        params = {"markets": ",".join(markets)}
        r = await self._client.get("/v1/ticker", params=params)
        r.raise_for_status()
        return r.json()

    async def get_krw_usdt_price(self) -> float:
        """Return current KRW price of 1 USDT."""
        tickers = await self.get_ticker(["KRW-USDT"])
        if tickers:
            return float(tickers[0].get("trade_price", 0.0))
        return 0.0

    # ── withdrawal fee ─────────────────────────────────────

    async def get_withdraw_chance(self, currency: str, net_type: str) -> dict[str, Any]:
        """Get withdrawal fee and minimum amount for a currency+network."""
        params = {"currency": currency, "net_type": net_type}
        return await self._get("/v1/withdraws/chance", params)
