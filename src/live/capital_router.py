"""Capital Router — master↔sub fund routing engine (skeleton).

This module owns the periodic *fund-flow* layer of the sub-account
topology. In its final form it will subsume :mod:`live.auto_sweep_engine`
and add policy-driven routing across multiple sub-accounts. For now we
land just the scaffolding so other components (allocator, API routes)
can integrate without waiting on the full migration.

Included in this skeleton:

* :class:`RoutingPolicy` — per-user configuration knobs.
* :class:`CapitalRouter` — long-lived engine with ``start`` / ``stop``
  hooks and a placeholder ``cycle`` method.
* :meth:`CapitalRouter.transfer` — the idempotent ``universal_transfer``
  helper. Every fund movement initiated by the router (or, in Phase 1,
  ad-hoc by the API) flows through this helper so we get a single audit
  trail (``wallet_transfers``) and consistent duplicate detection.

Deliberately *not* in this commit:

* Auto-sweep policy (Earn ↔ Futures rebalancing).
* Multi-sub topup decisions.
* Background loop body — the loop currently just logs a heartbeat.

Those land in a follow-up commit once we've validated this contract
against the API/UI layer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from binance.client_factory import (
    BinanceClientFactory,
    BinanceClientFactoryError,
    get_client_factory,
)
from binance.subaccount_client import (
    VALID_WALLET_TYPES,
    BinanceSubAccountClient,
    BinanceSubAccountClientError,
    WalletType,
)
from control.models import (
    WalletAccount,
    WalletRole,
    WalletTransfer,
    WalletTransferStatus,
)
from control.repo import (
    create_wallet_transfer,
    get_master_wallet_account,
    get_wallet_account,
    get_wallet_transfer_by_client_id,
    mark_wallet_transfer_failed,
    mark_wallet_transfer_succeeded,
)

_log = logging.getLogger("llmtrader.capital_router")

_DEFAULT_POLL_INTERVAL_SEC = 60
_DEFAULT_MIN_TRANSFER_USDT = Decimal("1")


class CapitalRouterError(RuntimeError):
    """Raised when the router cannot complete a fund movement."""


# ── policy ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class RoutingPolicy:
    """Per-user routing policy.

    All thresholds are denominated in USDT. The router holds one
    ``RoutingPolicy`` per active user; defaults match the conservative
    behaviour of the legacy auto-sweep engine so existing setups keep
    working when this policy first becomes effective.
    """

    master_spot_buffer_usdt: float = 100.0
    earn_min_subscribe_usdt: float = 50.0
    sub_futures_min_buffer_usdt: dict[str, float] = field(default_factory=dict)
    sub_futures_topup_threshold_usdt: dict[str, float] = field(default_factory=dict)
    max_transfers_per_cycle: int = 5
    min_transfer_usdt: Decimal = _DEFAULT_MIN_TRANSFER_USDT

    def buffer_for(self, alias: str, default: float = 50.0) -> float:
        return float(self.sub_futures_min_buffer_usdt.get(alias, default))

    def topup_threshold_for(self, alias: str, default: float = 30.0) -> float:
        return float(self.sub_futures_topup_threshold_usdt.get(alias, default))


# ── helpers ───────────────────────────────────────────────────────────


def _role_value(role: WalletRole | str) -> str:
    return role.value if isinstance(role, WalletRole) else role


async def _resolve_endpoint(
    session: AsyncSession,
    *,
    user_id: str,
    env: str,
    wallet_account_id: str | None,
    label: str,
) -> tuple[WalletAccount, str | None]:
    """Return ``(wallet_account, email_or_none)`` for one side of a transfer.

    ``wallet_account_id`` ``None`` is the sentinel for "master account".
    For sub accounts we require ``sub_account_email`` so Binance can
    target the right child wallet.
    """
    if wallet_account_id is None:
        master = await get_master_wallet_account(session, user_id=user_id, env=env)
        if master is None:
            raise CapitalRouterError(
                f"No master wallet for user={user_id} env={env} ({label} side)"
            )
        return master, None

    wallet = await get_wallet_account(session, wallet_account_id=wallet_account_id)
    if wallet is None:
        raise CapitalRouterError(
            f"Wallet not found: {wallet_account_id} ({label} side)"
        )
    if wallet.user_id != user_id or wallet.env != env:
        raise CapitalRouterError(
            f"Wallet {wallet_account_id} does not belong to user={user_id} env={env}"
        )
    if _role_value(wallet.role) == WalletRole.SUB.value and not wallet.sub_account_email:
        raise CapitalRouterError(
            f"Sub wallet {wallet_account_id} missing sub_account_email"
        )
    email = wallet.sub_account_email if _role_value(wallet.role) == WalletRole.SUB.value else None
    return wallet, email


def _validate_wallet_types(from_type: str, to_type: str) -> None:
    if from_type not in VALID_WALLET_TYPES:
        raise CapitalRouterError(f"invalid from_wallet_type: {from_type}")
    if to_type not in VALID_WALLET_TYPES:
        raise CapitalRouterError(f"invalid to_wallet_type: {to_type}")


def _generate_client_tran_id(user_id: str, reason: str) -> str:
    """Stable but unique idempotency key.

    The Binance ``clientTranId`` field is limited to 32 chars and must be
    alphanumeric. We compress the user id and reason into a short prefix
    and tack on a uuid4 fragment for uniqueness.
    """
    safe_user = "".join(ch for ch in user_id if ch.isalnum())[:8] or "user"
    safe_reason = "".join(ch for ch in reason if ch.isalnum())[:8] or "tx"
    return f"{safe_user}{safe_reason}{uuid.uuid4().hex[:14]}"


# ── engine ────────────────────────────────────────────────────────────


class CapitalRouter:
    """Long-lived router engine.

    The current implementation only exposes the transfer helper and a
    no-op cycle loop. Future commits flesh out :meth:`cycle` with the
    auto-sweep and multi-sub topup logic.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        client_factory: BinanceClientFactory | None = None,
        poll_interval_sec: int = _DEFAULT_POLL_INTERVAL_SEC,
    ) -> None:
        self._session_maker = session_maker
        self._client_factory = client_factory or get_client_factory()
        self._poll_interval_sec = poll_interval_sec
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            _log.warning("CapitalRouter already running")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="capital_router")
        _log.info("CapitalRouter started (interval=%ds)", self._poll_interval_sec)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        task = self._task
        self._task = None
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        _log.info("CapitalRouter stopped")

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.exception("CapitalRouter cycle failed: %s", exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_sec,
                )

    async def cycle(self) -> None:
        """One sweep pass.

        Placeholder implementation: emits a heartbeat log so operators can
        confirm the engine is wired up. Replaced in a follow-up commit
        with policy-driven routing.
        """
        _log.debug("CapitalRouter heartbeat")

    # ── idempotent transfer helper ───────────────────────────────

    async def transfer(  # noqa: PLR0913 — every kwarg models a distinct API/audit field
        self,
        session: AsyncSession,
        *,
        user_id: str,
        env: str,
        from_wallet_account_id: str | None,
        to_wallet_account_id: str | None,
        from_wallet_type: WalletType,
        to_wallet_type: WalletType,
        asset: str,
        amount: Decimal | float,
        reason: str,
        client_tran_id: str | None = None,
    ) -> WalletTransfer:
        """Perform an audited, idempotent universal transfer.

        Steps:

        1. Resolve from/to ``WalletAccount`` rows (None = master).
        2. Validate wallet types and amount.
        3. Build / accept a ``client_tran_id``; if a row with that id
           already exists, treat ``SUCCEEDED`` as a no-op return and
           ``FAILED`` as a hard error (caller must use a new id).
           ``PENDING`` rows are re-attempted (Binance dedupes on its
           side using the same ``client_tran_id``).
        4. Insert a PENDING ``wallet_transfers`` row.
        5. Call ``BinanceSubAccountClient.universal_transfer``.
        6. Mark the row SUCCEEDED with ``binance_tran_id`` on success, or
           FAILED with the error message on failure.

        Returns the final ``WalletTransfer`` row (refreshed).
        """
        amount_dec = Decimal(str(amount))
        if amount_dec <= 0:
            raise CapitalRouterError(f"amount must be > 0 (got {amount_dec})")

        _validate_wallet_types(from_wallet_type, to_wallet_type)

        from_wallet, from_email = await _resolve_endpoint(
            session,
            user_id=user_id,
            env=env,
            wallet_account_id=from_wallet_account_id,
            label="from",
        )
        to_wallet, to_email = await _resolve_endpoint(
            session,
            user_id=user_id,
            env=env,
            wallet_account_id=to_wallet_account_id,
            label="to",
        )

        if from_wallet.id == to_wallet.id and from_wallet_type == to_wallet_type:
            raise CapitalRouterError(
                "transfer source and destination are identical"
            )

        tran_id = client_tran_id or _generate_client_tran_id(user_id, reason)

        existing = await get_wallet_transfer_by_client_id(
            session,
            client_tran_id=tran_id,
        )
        if existing is not None:
            status = existing.status
            status_value = status.value if isinstance(status, WalletTransferStatus) else status
            if status_value == WalletTransferStatus.SUCCEEDED.value:
                _log.info(
                    "transfer %s already SUCCEEDED — returning cached row",
                    tran_id,
                )
                return existing
            if status_value == WalletTransferStatus.FAILED.value:
                raise CapitalRouterError(
                    f"transfer {tran_id} previously FAILED; pick a new client_tran_id"
                )
            transfer = existing
            _log.warning(
                "transfer %s found in PENDING state — retrying through Binance dedup",
                tran_id,
            )
        else:
            transfer = await create_wallet_transfer(
                session,
                user_id=user_id,
                from_wallet_account_id=from_wallet.id,
                to_wallet_account_id=to_wallet.id,
                from_wallet_type=from_wallet_type,
                to_wallet_type=to_wallet_type,
                asset=asset,
                amount=amount_dec,
                reason=reason,
                client_tran_id=tran_id,
                status=WalletTransferStatus.PENDING,
            )
            await session.commit()

        try:
            client = await self._client_factory.get_master_subaccount_client(
                session,
                user_id=user_id,
                env=env,
            )
        except BinanceClientFactoryError as exc:
            await self._fail_transfer(session, transfer.id, str(exc))
            raise CapitalRouterError(str(exc)) from exc

        try:
            response = await self._call_universal_transfer(
                client,
                from_email=from_email,
                to_email=to_email,
                from_wallet_type=from_wallet_type,
                to_wallet_type=to_wallet_type,
                asset=asset,
                amount=amount_dec,
                client_tran_id=tran_id,
            )
        except (BinanceSubAccountClientError, httpx.HTTPError) as exc:
            await self._fail_transfer(session, transfer.id, str(exc))
            raise CapitalRouterError(f"universal_transfer failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            await self._fail_transfer(session, transfer.id, repr(exc))
            raise

        binance_tran_id = self._extract_tran_id(response)
        updated = await mark_wallet_transfer_succeeded(
            session,
            wallet_transfer_id=transfer.id,
            binance_tran_id=binance_tran_id,
        )
        await session.commit()
        _log.info(
            "transfer %s SUCCEEDED user=%s amount=%s %s reason=%s",
            tran_id,
            user_id,
            amount_dec,
            asset,
            reason,
        )
        return updated or transfer

    # ── internals ────────────────────────────────────────────────

    async def _call_universal_transfer(  # noqa: PLR0913 — pass-through wrapper
        self,
        client: BinanceSubAccountClient,
        *,
        from_email: str | None,
        to_email: str | None,
        from_wallet_type: WalletType,
        to_wallet_type: WalletType,
        asset: str,
        amount: Decimal,
        client_tran_id: str,
    ) -> dict[str, Any]:
        return await client.universal_transfer(
            from_account_type=from_wallet_type,
            to_account_type=to_wallet_type,
            asset=asset,
            amount=amount,
            from_email=from_email,
            to_email=to_email,
            client_tran_id=client_tran_id,
        )

    async def _fail_transfer(
        self,
        session: AsyncSession,
        wallet_transfer_id: str,
        error_message: str,
    ) -> None:
        try:
            await mark_wallet_transfer_failed(
                session,
                wallet_transfer_id=wallet_transfer_id,
                error_message=error_message,
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "failed to persist FAILED status for transfer %s: %s",
                wallet_transfer_id,
                exc,
            )

    @staticmethod
    def _extract_tran_id(response: dict[str, Any]) -> str | None:
        for key in ("tranId", "txnId", "id"):
            value = response.get(key)
            if value is not None:
                return str(value)
        return None


# ── module-level singleton (mirrors auto_sweep_engine API) ────────────

_router: CapitalRouter | None = None


def get_capital_router(
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> CapitalRouter:
    """Return (and lazily create) the process-wide :class:`CapitalRouter`."""
    global _router  # noqa: PLW0603 — module-level singleton accessor
    if _router is None:
        if session_maker is None:
            raise CapitalRouterError(
                "session_maker required for first get_capital_router() call"
            )
        _router = CapitalRouter(session_maker=session_maker)
    return _router


async def start_capital_router(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    router = get_capital_router(session_maker)
    await router.start()


async def stop_capital_router() -> None:
    global _router  # noqa: PLW0603 — module-level singleton accessor
    if _router is None:
        return
    router = _router
    _router = None
    await router.stop()
