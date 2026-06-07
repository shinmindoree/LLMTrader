"""Binance Sub-account REST client (master-key only sapi endpoints).

This client wraps the subset of ``/sapi/v1/sub-account/*`` endpoints that
the Capital Router and onboarding flow need:

  - Account management: create virtual sub-account, list, enable
    futures/options, query status / position risk.
  - API key IP restriction: get / add / delete (the trading key itself must
    be created manually by the user in the Binance web UI — retail master
    accounts cannot programmatically create sub-account API keys).
  - Asset management: universalTransfer (master↔sub, sub↔sub, between
    wallet types), get sub assets / sub futures account.

It deliberately mirrors :mod:`binance.earn_client` for HMAC signing, time
sync, and httpx lifecycle so the two can share future helpers.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

_log = logging.getLogger("llmtrader.binance.subaccount")

SPOT_MAINNET_BASE = "https://api.binance.com"

WalletType = str  # "SPOT" | "USDT_FUTURE" | "COIN_FUTURE" | "MARGIN" | "ISOLATED_MARGIN"

VALID_WALLET_TYPES: frozenset[WalletType] = frozenset(
    {"SPOT", "USDT_FUTURE", "COIN_FUTURE", "MARGIN", "ISOLATED_MARGIN"}
)


class BinanceSubAccountClientError(RuntimeError):
    pass


class BinanceSubAccountClient:
    """Wrapper for master-key-only sub-account endpoints.

    All endpoints are authenticated with the master account's API key (the
    sub-account API keys created in the web UI cannot call these). The
    master key needs the "Permits Universal Transfer" option enabled.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = SPOT_MAINNET_BASE,
        timeout: float = 10.0,
    ) -> None:
        if not api_key or not api_secret:
            raise BinanceSubAccountClientError("api_key and api_secret required")
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"X-MBX-APIKEY": api_key},
        )
        self._time_offset_ms: int = 0
        self._last_sync_ts: float = 0.0
        self._sync_interval_sec: float = 300.0

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── signing / time sync ────────────────────────────────

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
            _log.warning("SubAccount client time sync failed: %s", exc)

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        params.setdefault("timestamp", self._timestamp())
        params.setdefault("recvWindow", 60000)
        qs = urlencode(params, doseq=True)
        sig = hmac.new(self._api_secret, qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    async def _signed(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        await self._maybe_sync_time()
        signed = self._sign(dict(params or {}))
        r = await self._client.request(method, path, params=signed)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text}
            raise BinanceSubAccountClientError(
                f"{method} {path} -> {r.status_code}: {data}"
            )
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    # ── account management ────────────────────────────────

    async def create_virtual_subaccount(self, alias_string: str) -> str:
        """Create a virtual sub-account and return the generated email.

        Binance generates a synthetic email of the form
        ``{alias_string}_virtual@...email.com``. The same ``alias_string``
        is rejected if it would collide with an existing virtual email, so
        callers should include a per-user nonce when collisions are possible.
        """
        if not alias_string or len(alias_string) > 64:
            raise BinanceSubAccountClientError("alias_string must be 1..64 chars")
        data = await self._signed(
            "POST",
            "/sapi/v1/sub-account/virtualSubAccount",
            {"subAccountString": alias_string},
        )
        email = str(data.get("email") or "").strip()
        if not email:
            raise BinanceSubAccountClientError(
                f"virtualSubAccount returned no email: {data!r}"
            )
        return email

    async def list_subaccounts(
        self,
        *,
        email: str | None = None,
        is_freeze: bool | None = None,
        page_size: int = 200,
    ) -> list[dict[str, Any]]:
        """Return all sub-accounts (paginated under the hood).

        Binance caps ``limit`` at 200; this helper transparently iterates
        pages until an empty page is returned. ``email`` and ``is_freeze``
        narrow the result set server-side.
        """
        if page_size < 1 or page_size > 200:  # noqa: PLR2004 — Binance hard limit
            raise BinanceSubAccountClientError(
                "page_size must be between 1 and 200"
            )
        all_rows: list[dict[str, Any]] = []
        page = 1
        while True:
            params: dict[str, Any] = {"page": page, "limit": page_size}
            if email:
                params["email"] = email
            if is_freeze is not None:
                params["isFreeze"] = "true" if is_freeze else "false"
            data = await self._signed(
                "GET", "/sapi/v1/sub-account/list", params
            )
            rows = data.get("subAccounts") if isinstance(data, dict) else None
            rows = list(rows or [])
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            page += 1
            if page > 50:  # noqa: PLR2004 — sanity stop at 10000 subs
                _log.warning("list_subaccounts hit page cap (50); truncating")
                break
        return all_rows

    async def enable_futures(self, email: str) -> dict[str, Any]:
        return await self._signed(
            "POST", "/sapi/v1/sub-account/futures/enable", {"email": email}
        )

    async def enable_options(self, email: str) -> dict[str, Any]:
        return await self._signed(
            "POST", "/sapi/v1/sub-account/eoptions/enable", {"email": email}
        )

    async def get_status(self, email: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if email:
            params["email"] = email
        data = await self._signed("GET", "/sapi/v1/sub-account/status", params)
        return list(data) if isinstance(data, list) else []

    # ── API key IP restriction (master key controls sub keys' IPs) ──

    async def get_ip_restriction(
        self, *, email: str, sub_api_key: str
    ) -> dict[str, Any]:
        return await self._signed(
            "GET",
            "/sapi/v1/sub-account/subAccountApi/ipRestriction",
            {"email": email, "subAccountApiKey": sub_api_key},
        )

    async def add_ip_restriction(
        self, *, email: str, sub_api_key: str, ip_addresses: list[str]
    ) -> dict[str, Any]:
        if not ip_addresses:
            raise BinanceSubAccountClientError("ip_addresses must be non-empty")
        return await self._signed(
            "POST",
            "/sapi/v2/sub-account/subAccountApi/ipRestriction",
            {
                "email": email,
                "subAccountApiKey": sub_api_key,
                "status": "2",  # 2 = restrict access to specified IPs
                "ipAddress": ",".join(ip_addresses),
            },
        )

    async def delete_ip_restriction(
        self, *, email: str, sub_api_key: str, ip_addresses: list[str]
    ) -> dict[str, Any]:
        if not ip_addresses:
            raise BinanceSubAccountClientError("ip_addresses must be non-empty")
        return await self._signed(
            "DELETE",
            "/sapi/v1/sub-account/subAccountApi/ipRestriction/ipList",
            {
                "email": email,
                "subAccountApiKey": sub_api_key,
                "ipAddress": ",".join(ip_addresses),
            },
        )

    # ── asset management ──────────────────────────────────

    async def universal_transfer(  # noqa: PLR0913 — all kwargs are required by the Binance API contract
        self,
        *,
        from_account_type: WalletType,
        to_account_type: WalletType,
        asset: str,
        amount: Decimal | float,
        from_email: str | None = None,
        to_email: str | None = None,
        client_tran_id: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Master-key universal transfer between master/sub and wallet types.

        Endpoint: ``POST /sapi/v1/sub-account/universalTransfer`` (master only).

        ``from_email`` / ``to_email`` default to the master account when
        omitted. At least one of them must be set if ``from_account_type``
        and ``to_account_type`` are identical.

        ``client_tran_id`` is the idempotency key Binance uses to dedupe
        retries — callers should always pass a stable per-intent value.
        """
        if from_account_type not in VALID_WALLET_TYPES:
            raise BinanceSubAccountClientError(
                f"invalid from_account_type: {from_account_type}"
            )
        if to_account_type not in VALID_WALLET_TYPES:
            raise BinanceSubAccountClientError(
                f"invalid to_account_type: {to_account_type}"
            )
        if (
            from_account_type == to_account_type
            and not from_email
            and not to_email
        ):
            raise BinanceSubAccountClientError(
                "from_email or to_email required when wallet types match"
            )
        amt = Decimal(str(amount))
        if amt <= 0:
            raise BinanceSubAccountClientError("amount must be > 0")

        payload: dict[str, Any] = {
            "fromAccountType": from_account_type,
            "toAccountType": to_account_type,
            "asset": asset,
            "amount": format(amt, "f"),
        }
        if from_email:
            payload["fromEmail"] = from_email
        if to_email:
            payload["toEmail"] = to_email
        if client_tran_id:
            payload["clientTranId"] = client_tran_id
        if symbol:
            payload["symbol"] = symbol
        return await self._signed(
            "POST", "/sapi/v1/sub-account/universalTransfer", payload
        )

    async def get_subaccount_assets(self, email: str) -> dict[str, Any]:
        """Return sub-account balances (Spot/Margin)."""
        return await self._signed(
            "GET", "/sapi/v3/sub-account/assets", {"email": email}
        )

    async def get_sub_futures_account(
        self, email: str, *, futures_type: int = 1
    ) -> dict[str, Any]:
        """Return sub-account USD-M (1) or COIN-M (2) futures account summary."""
        return await self._signed(
            "GET",
            "/sapi/v2/sub-account/futures/account",
            {"email": email, "futuresType": futures_type},
        )

    async def get_sub_futures_position_risk(
        self, email: str, *, futures_type: int = 1
    ) -> list[dict[str, Any]]:
        """Return sub-account futures position risk snapshot (V2)."""
        data = await self._signed(
            "GET",
            "/sapi/v2/sub-account/futures/positionRisk",
            {"email": email, "futuresType": futures_type},
        )
        if isinstance(data, dict):
            for key in ("futurePositionRiskVos", "positions"):
                if key in data and isinstance(data[key], list):
                    return list(data[key])
            return []
        return list(data) if isinstance(data, list) else []
