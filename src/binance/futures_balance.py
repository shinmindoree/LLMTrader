"""Thin signed-GET helpers for master Futures account snapshots.

Both USDⓈ-M Futures (``fapi``) and COIN-M Futures (``dapi``) live on
host-specific base URLs that don't match the Spot/SAPI client we already
have around. This module exposes minimal, single-shot helpers so the
wallet-balance aggregator can fetch multi-asset snapshots without taking
a dependency on the higher-level trading client classes.

The helpers create and dispose of their own ``httpx.AsyncClient`` and are
intentionally narrow (one signed GET each) — we do not cache and we do
not implement order/transfer endpoints here.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

UM_FUTURES_BASE = "https://fapi.binance.com"
CM_FUTURES_BASE = "https://dapi.binance.com"


class BinanceFuturesBalanceError(RuntimeError):
    """Raised when one of the signed snapshot calls returns a non-2xx."""


async def _signed_get(
    *,
    api_key: str,
    api_secret: str,
    base_url: str,
    path: str,
    timeout: float = 10.0,  # noqa: ASYNC109 — forwarded to httpx, not used as cancel scope
) -> Any:
    """Perform a single signed GET against ``base_url`` + ``path``."""
    if not api_key or not api_secret:
        raise BinanceFuturesBalanceError("api_key and api_secret required")
    params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
    query = urlencode(params, doseq=True)
    signature = hmac.new(
        api_secret.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    signed = f"{query}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        r = await client.get(f"{path}?{signed}", headers=headers)
        if r.status_code >= 400:
            try:
                data: Any = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text}
            raise BinanceFuturesBalanceError(
                f"GET {path} -> {r.status_code}: {data}"
            )
        return r.json()


async def fetch_um_futures_account(
    *,
    api_key: str,
    api_secret: str,
    timeout: float = 10.0,  # noqa: ASYNC109 — forwarded to httpx
) -> dict[str, Any]:
    """Return the ``/fapi/v2/account`` snapshot (master USDⓈ-M Futures).

    The shape is ``{"assets": [{asset, walletBalance, availableBalance, ...}, ...]}``
    so the caller can hand it to ``_cells_from_futures``.
    """
    return await _signed_get(
        api_key=api_key,
        api_secret=api_secret,
        base_url=UM_FUTURES_BASE,
        path="/fapi/v2/account",
        timeout=timeout,
    )


async def fetch_cm_futures_account(
    *,
    api_key: str,
    api_secret: str,
    timeout: float = 10.0,  # noqa: ASYNC109 — forwarded to httpx
) -> dict[str, Any]:
    """Return the ``/dapi/v1/account`` snapshot (master COIN-M Futures).

    The shape is ``{"assets": [...], "positions": [...]}`` — symmetrical to
    the USD-M endpoint above for our cell-extractor's purposes.
    """
    return await _signed_get(
        api_key=api_key,
        api_secret=api_secret,
        base_url=CM_FUTURES_BASE,
        path="/dapi/v1/account",
        timeout=timeout,
    )
