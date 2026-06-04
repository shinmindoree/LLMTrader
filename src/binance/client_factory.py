"""Per-wallet Binance client factory with LRU-style caching.

Earlier code instantiated :class:`binance.client.BinanceHTTPClient` and
:class:`binance.earn_client.BinanceEarnClient` ad-hoc from
``BinanceApiCredential`` rows. With the sub-account topology we now have
*multiple* key pairs per user (one master + N subs), so callers need a
uniform way to obtain the right client for a given ``wallet_account_id``.

This factory:

* Caches clients by ``wallet_account_id`` so the same long-lived ``httpx``
  ``AsyncClient`` is reused across calls.
* Invalidates a cached client when the underlying ``WalletAccount`` row's
  ``updated_at`` (or status) changes — that's how we react to key rotation.
* Refuses to hand out a master-only :class:`BinanceSubAccountClient` for a
  sub wallet (and vice versa).
* Exposes :func:`invalidate` for explicit eviction and :func:`aclose_all`
  for graceful shutdown.

It deliberately stays decoupled from the Capital Router / Allocator —
those modules just ask for the right client and trust the factory to keep
it fresh.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from binance.client import BinanceHTTPClient
from binance.earn_client import BinanceEarnClient
from binance.subaccount_client import BinanceSubAccountClient
from common.crypto import get_crypto_service
from control.models import WalletAccount, WalletAccountStatus, WalletRole
from control.repo import get_master_wallet_account, get_wallet_account

_log = logging.getLogger("llmtrader.binance.client_factory")


class BinanceClientFactoryError(RuntimeError):
    """Raised when a wallet cannot produce a usable client."""


_FUTURES_BASE_URLS: dict[str, str] = {
    "mainnet": "https://fapi.binance.com",
    "testnet": "https://testnet.binancefuture.com",
}

_SPOT_BASE_URLS: dict[str, str] = {
    "mainnet": "https://api.binance.com",
    "testnet": "https://testnet.binance.vision",
}


def _futures_base_url(env: str) -> str:
    return _FUTURES_BASE_URLS.get(env, _FUTURES_BASE_URLS["testnet"])


def _spot_base_url(env: str) -> str:
    return _SPOT_BASE_URLS.get(env, _SPOT_BASE_URLS["testnet"])


def _status_value(status: WalletAccountStatus | str) -> str:
    return status.value if isinstance(status, WalletAccountStatus) else status


def _role_value(role: WalletRole | str) -> str:
    return role.value if isinstance(role, WalletRole) else role


@dataclass(slots=True)
class _CachedClient:
    """One cached client + the wallet fingerprint it was built from."""

    client: Any
    api_key: str
    updated_at: datetime | None
    status: str


class BinanceClientFactory:
    """Async-safe cache of per-wallet Binance clients.

    Three flavors are tracked separately so a single wallet can have a
    futures client *and* a spot client live at the same time.
    """

    def __init__(self) -> None:
        self._trading: dict[str, _CachedClient] = {}
        self._spot: dict[str, _CachedClient] = {}
        self._subaccount: dict[str, _CachedClient] = {}
        self._lock = asyncio.Lock()

    # ── public API ────────────────────────────────────────────────────

    async def get_trading_client(
        self,
        session: AsyncSession,
        *,
        wallet_account_id: str,
    ) -> BinanceHTTPClient:
        """Return the USDⓈ-M futures client (``fapi.binance.com``) for a wallet."""
        wallet = await self._load_active_wallet(session, wallet_account_id)
        api_key, api_secret = self._decrypt_keys(wallet)
        async with self._lock:
            cached = self._trading.get(wallet_account_id)
            if cached and self._is_fresh(cached, wallet, api_key):
                return cached.client
            if cached is not None:
                await self._safe_close(cached.client)
            client = BinanceHTTPClient(
                api_key=api_key,
                api_secret=api_secret,
                base_url=_futures_base_url(wallet.env),
            )
            self._trading[wallet_account_id] = _CachedClient(
                client=client,
                api_key=api_key,
                updated_at=wallet.updated_at,
                status=_status_value(wallet.status),
            )
            return client

    async def get_spot_client(
        self,
        session: AsyncSession,
        *,
        wallet_account_id: str,
    ) -> BinanceEarnClient:
        """Return the Spot + Earn + Universal Transfer client for a wallet.

        Currently only the mainnet spot host is wired into
        :class:`BinanceEarnClient`; on testnet we still construct the client
        but the caller should expect Earn endpoints to be unavailable. The
        factory does not enforce that — it just hands out the right keys.
        """
        wallet = await self._load_active_wallet(session, wallet_account_id)
        api_key, api_secret = self._decrypt_keys(wallet)
        async with self._lock:
            cached = self._spot.get(wallet_account_id)
            if cached and self._is_fresh(cached, wallet, api_key):
                return cached.client
            if cached is not None:
                await self._safe_close(cached.client)
            client = BinanceEarnClient(
                api_key=api_key,
                api_secret=api_secret,
                base_url=_spot_base_url(wallet.env),
                futures_base_url=_futures_base_url(wallet.env),
            )
            self._spot[wallet_account_id] = _CachedClient(
                client=client,
                api_key=api_key,
                updated_at=wallet.updated_at,
                status=_status_value(wallet.status),
            )
            return client

    async def get_master_subaccount_client(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        env: str,
    ) -> BinanceSubAccountClient:
        """Return the master-key-only sub-account client for a user/env pair.

        Looks up the user's master ``WalletAccount`` and returns a cached
        :class:`BinanceSubAccountClient`. Raises
        :class:`BinanceClientFactoryError` if no active master wallet exists.
        """
        master = await get_master_wallet_account(session, user_id=user_id, env=env)
        if master is None:
            raise BinanceClientFactoryError(
                f"No master wallet configured for user={user_id} env={env}"
            )
        self._require_active(master)
        if _role_value(master.role) != WalletRole.MASTER.value:
            raise BinanceClientFactoryError(
                f"Wallet {master.id} is not a master account"
            )
        api_key, api_secret = self._decrypt_keys(master)
        async with self._lock:
            cached = self._subaccount.get(master.id)
            if cached and self._is_fresh(cached, master, api_key):
                return cached.client
            if cached is not None:
                await self._safe_close(cached.client)
            client = BinanceSubAccountClient(
                api_key=api_key,
                api_secret=api_secret,
                base_url=_spot_base_url(master.env),
            )
            self._subaccount[master.id] = _CachedClient(
                client=client,
                api_key=api_key,
                updated_at=master.updated_at,
                status=_status_value(master.status),
            )
            return client

    async def invalidate(self, wallet_account_id: str) -> None:
        """Drop every cached client for ``wallet_account_id`` and close them."""
        async with self._lock:
            entries = [
                self._trading.pop(wallet_account_id, None),
                self._spot.pop(wallet_account_id, None),
                self._subaccount.pop(wallet_account_id, None),
            ]
        for entry in entries:
            if entry is not None:
                await self._safe_close(entry.client)

    async def aclose_all(self) -> None:
        """Close every cached client. Call from app shutdown."""
        async with self._lock:
            buckets = (self._trading, self._spot, self._subaccount)
            entries = [entry for bucket in buckets for entry in bucket.values()]
            for bucket in buckets:
                bucket.clear()
        for entry in entries:
            await self._safe_close(entry.client)

    # ── helpers ───────────────────────────────────────────────────────

    async def _load_active_wallet(
        self,
        session: AsyncSession,
        wallet_account_id: str,
    ) -> WalletAccount:
        wallet = await get_wallet_account(session, wallet_account_id=wallet_account_id)
        if wallet is None:
            raise BinanceClientFactoryError(
                f"Wallet account not found: {wallet_account_id}"
            )
        self._require_active(wallet)
        return wallet

    @staticmethod
    def _require_active(wallet: WalletAccount) -> None:
        status = _status_value(wallet.status)
        if status != WalletAccountStatus.ACTIVE.value:
            raise BinanceClientFactoryError(
                f"Wallet {wallet.id} is not active (status={status})"
            )
        if not wallet.api_key_enc or not wallet.api_secret_enc:
            raise BinanceClientFactoryError(
                f"Wallet {wallet.id} has no API key material"
            )

    @staticmethod
    def _decrypt_keys(wallet: WalletAccount) -> tuple[str, str]:
        crypto = get_crypto_service()
        try:
            api_key = crypto.decrypt(wallet.api_key_enc)
            api_secret = crypto.decrypt(wallet.api_secret_enc)
        except Exception as exc:  # noqa: BLE001
            raise BinanceClientFactoryError(
                f"Failed to decrypt keys for wallet {wallet.id}: {exc}"
            ) from exc
        return api_key, api_secret

    @staticmethod
    def _is_fresh(
        cached: _CachedClient,
        wallet: WalletAccount,
        api_key: str,
    ) -> bool:
        """Return True iff the cached client still matches the wallet row."""
        return (
            cached.api_key == api_key
            and cached.status == _status_value(wallet.status)
            and cached.updated_at == wallet.updated_at
        )

    @staticmethod
    async def _safe_close(client: Any) -> None:
        aclose = getattr(client, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001
            _log.warning("client aclose failed: %s", exc)


# ── module-level singleton ────────────────────────────────────────────

_factory: BinanceClientFactory | None = None


def get_client_factory() -> BinanceClientFactory:
    """Return the process-wide :class:`BinanceClientFactory` singleton."""
    global _factory  # noqa: PLW0603 — module-level singleton accessor
    if _factory is None:
        _factory = BinanceClientFactory()
    return _factory


async def shutdown_client_factory() -> None:
    """Close every cached client and drop the singleton."""
    global _factory  # noqa: PLW0603 — module-level singleton accessor
    if _factory is None:
        return
    factory = _factory
    _factory = None
    await factory.aclose_all()
