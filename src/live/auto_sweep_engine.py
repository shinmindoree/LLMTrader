"""Auto-Sweep background engine — Futures-wallet based.

Every 5 minutes:
  1. List all users with `auto_sweep_enabled = true`
  2. For each user (isolated via try/except):
     - Decrypt mainnet Binance keys (skip testnet)
     - Fetch Futures available_balance + current Simple Earn position
     - Decide:
         futures > futures_buffer + sweep_threshold
             → transfer excess Futures → Spot, then subscribe to Simple Earn
         futures < futures_buffer and earn > 0
             → redeem from Simple Earn, then transfer Spot → Futures to top up
         otherwise → noop
     - Persist outcome to account_snapshots["auto_sweep:{user_id}"]
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from binance.earn_client import BinanceEarnClient, BinanceEarnClientError
from common.crypto import get_crypto_service
from control.models import UserProfile
from control.repo import (
    get_account_snapshot,
    list_auto_sweep_enabled_users,
    upsert_account_snapshot,
)

_log = logging.getLogger("llmtrader.auto_sweep")

_POLL_INTERVAL_SEC = 300  # 5 minutes
_MIN_TXN_USDT = 1.0  # minimum transaction size to avoid dust transfers
TESTNET_HOST_HINTS = ("testnet",)


def _is_testnet(base_url: str | None) -> bool:
    if not base_url:
        return False
    return any(h in base_url.lower() for h in TESTNET_HOST_HINTS)


def snapshot_key(user_id: str) -> str:
    return f"auto_sweep:{user_id}"


@dataclass
class _EngineState:
    running: bool = False
    _task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)


_state = _EngineState()


# ── Public interface ───────────────────────────────────────


async def start_engine(session_maker: async_sessionmaker[AsyncSession]) -> None:
    global _state  # noqa: PLW0603
    if _state.running:
        _log.warning("Auto-sweep engine already running")
        return
    _state = _EngineState(running=True)
    _state._task = asyncio.create_task(_loop(session_maker), name="auto_sweep_engine")
    _log.info("Auto-sweep engine started (interval=%ds)", _POLL_INTERVAL_SEC)


async def stop_engine() -> None:
    if not _state.running:
        return
    _state.running = False
    if _state._task and not _state._task.done():
        _state._task.cancel()
        try:
            await _state._task
        except asyncio.CancelledError:
            pass
    _log.info("Auto-sweep engine stopped")


async def get_user_status(session: AsyncSession, *, user_id: str) -> dict[str, Any] | None:
    snap = await get_account_snapshot(session, key=snapshot_key(user_id))
    if not snap:
        return None
    return snap.data_json


# ── Loop ───────────────────────────────────────────────────


async def _loop(session_maker: async_sessionmaker[AsyncSession]) -> None:
    while _state.running:
        try:
            await _run_cycle(session_maker)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _log.exception("auto-sweep cycle failed: %s", exc)
        try:
            await asyncio.sleep(_POLL_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise


async def _run_cycle(session_maker: async_sessionmaker[AsyncSession]) -> None:
    async with session_maker() as session:
        users = await list_auto_sweep_enabled_users(session)
    if not users:
        return
    _log.info("auto-sweep cycle: %d user(s)", len(users))
    for user in users:
        try:
            await _process_user(session_maker, user)
        except Exception as exc:  # noqa: BLE001
            _log.exception("auto-sweep failed for user=%s: %s", user.user_id, exc)
            await _record_error(session_maker, user.user_id, str(exc))


async def _process_user(
    session_maker: async_sessionmaker[AsyncSession], user: UserProfile
) -> None:
    if _is_testnet(user.binance_base_url):
        await _record_error(
            session_maker, user.user_id, "Auto-sweep disabled: mainnet keys required"
        )
        return
    if not user.binance_api_key_enc or not user.binance_api_secret_enc:
        await _record_error(session_maker, user.user_id, "No Binance keys configured")
        return

    crypto = get_crypto_service()
    try:
        api_key = crypto.decrypt(user.binance_api_key_enc)
        api_secret = crypto.decrypt(user.binance_api_secret_enc)
    except Exception as exc:  # noqa: BLE001
        await _record_error(session_maker, user.user_id, f"Key decryption failed: {exc}")
        return

    futures_buffer = float(user.auto_sweep_futures_buffer_usdt)
    sweep_threshold = float(user.auto_sweep_sweep_threshold_usdt)

    client = BinanceEarnClient(api_key=api_key, api_secret=api_secret)
    try:
        futures_usdt = await client.fetch_futures_available_balance()
        earn_usdt = 0.0
        try:
            earn_usdt = await client.fetch_flexible_position_usdt()
        except BinanceEarnClientError as exc:
            _log.warning("flex position fetch failed user=%s: %s", user.user_id, exc)

        action = "noop"
        detail: dict[str, Any] = {}

        if futures_usdt > futures_buffer + sweep_threshold:
            # Sweep excess: Futures → Spot → Simple Earn
            transfer_amount = futures_usdt - futures_buffer
            if transfer_amount >= _MIN_TXN_USDT:
                await client.transfer_futures_to_spot(transfer_amount)
                _log.info(
                    "transferred Futures→Spot user=%s amount=%.2f",
                    user.user_id, transfer_amount,
                )
                product_id = await client.get_usdt_flexible_product_id()
                if not product_id:
                    raise BinanceEarnClientError("No USDT Flexible product available")
                detail = await client.subscribe(transfer_amount, product_id)
                action = "subscribed"
                _log.info(
                    "subscribed user=%s amount=%.2f product=%s",
                    user.user_id, transfer_amount, product_id,
                )

        elif futures_usdt < futures_buffer and earn_usdt > 0:
            # Top up: Simple Earn → Spot → Futures
            need = min(futures_buffer - futures_usdt, earn_usdt)
            if need >= _MIN_TXN_USDT:
                product_id = await client.get_usdt_flexible_product_id()
                if not product_id:
                    raise BinanceEarnClientError("No USDT Flexible product available")
                detail = await client.redeem(need, product_id)
                _log.info(
                    "redeemed user=%s amount=%.2f product=%s",
                    user.user_id, need, product_id,
                )
                await client.transfer_spot_to_futures(need)
                _log.info(
                    "transferred Spot→Futures user=%s amount=%.2f",
                    user.user_id, need,
                )
                action = "redeemed"

        await _record_success(
            session_maker,
            user.user_id,
            futures_usdt=futures_usdt,
            earn_usdt=earn_usdt,
            futures_buffer_usdt=futures_buffer,
            sweep_threshold_usdt=sweep_threshold,
            action=action,
            detail=detail,
        )
    finally:
        await client.aclose()


# ── Snapshot persistence ───────────────────────────────────


async def _record_success(
    session_maker: async_sessionmaker[AsyncSession],
    user_id: str,
    *,
    futures_usdt: float,
    earn_usdt: float,
    futures_buffer_usdt: float,
    sweep_threshold_usdt: float,
    action: str,
    detail: dict[str, Any],
) -> None:
    payload = {
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "futures_usdt": futures_usdt,
        "earn_usdt": earn_usdt,
        "futures_buffer_usdt": futures_buffer_usdt,
        "sweep_threshold_usdt": sweep_threshold_usdt,
        "last_action": action,
        "last_error": None,
        "detail": detail,
    }
    async with session_maker() as session:
        await upsert_account_snapshot(session, key=snapshot_key(user_id), data_json=payload)
        await session.commit()


async def _record_error(
    session_maker: async_sessionmaker[AsyncSession], user_id: str, message: str
) -> None:
    async with session_maker() as session:
        existing = await get_account_snapshot(session, key=snapshot_key(user_id))
        base = dict(existing.data_json) if existing and existing.data_json else {}
        base.update(
            {
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "last_action": "error",
                "last_error": message,
            }
        )
        await upsert_account_snapshot(session, key=snapshot_key(user_id), data_json=base)
        await session.commit()
