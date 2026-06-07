"""Capital Router — master↔sub fund routing engine.

Owns the periodic *fund-flow* layer of the sub-account topology:

* :class:`RoutingPolicy` — per-user configuration knobs.
* :class:`CapitalRouter` — long-lived engine with ``start`` / ``stop``
  hooks and a real ``cycle`` that absorbs the legacy auto-sweep loop.
* :meth:`CapitalRouter.transfer` — the idempotent ``universal_transfer``
  helper. Every fund movement initiated by the router (or, in Phase 1,
  ad-hoc by the API) flows through this helper so we get a single audit
  trail (``wallet_transfers``) and consistent duplicate detection.

``cycle`` handles two topologies:

* **Master-only (legacy)** — user has no sub wallets. The cycle runs the
  exact Futures↔Spot↔SimpleEarn flow that ``live.auto_sweep_engine``
  used, against the master's Binance keys. Existing single-account users
  see no behaviour change.
* **Sub-aware** — user has one or more active sub wallets. The cycle
  inspects each sub's Futures balance via the master sub-account API,
  and rebalances master↔sub through :meth:`transfer` (audited /
  idempotent via ``wallet_transfers``). SimpleEarn subscribe/redeem
  still happens on the master side.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from binance.client_factory import (
    BinanceClientFactory,
    BinanceClientFactoryError,
    get_client_factory,
)
from binance.earn_client import BinanceEarnClient, BinanceEarnClientError
from binance.subaccount_client import (
    VALID_WALLET_TYPES,
    BinanceSubAccountClient,
    BinanceSubAccountClientError,
    WalletType,
)
from common.crypto import get_crypto_service
from control.models import (
    UserProfile,
    WalletAccount,
    WalletAccountStatus,
    WalletRole,
    WalletTransfer,
    WalletTransferStatus,
)
from control.repo import (
    create_wallet_transfer,
    get_account_snapshot,
    get_binance_credential,
    get_master_wallet_account,
    get_user_profile,
    get_wallet_account,
    get_wallet_transfer_by_client_id,
    list_auto_sweep_enabled_users,
    list_wallet_accounts,
    mark_wallet_transfer_failed,
    mark_wallet_transfer_succeeded,
    upsert_account_snapshot,
)
from live.wallet_reconciler import (
    ReconcileSummary,
    WalletReconciler,
    get_wallet_reconciler,
)

_log = logging.getLogger("llmtrader.capital_router")

_DEFAULT_POLL_INTERVAL_SEC = 60
_DEFAULT_MIN_TRANSFER_USDT = Decimal("1")
_MIN_LEGACY_TXN_USDT = 1.0


def _snapshot_key(user_id: str) -> str:
    """Snapshot key shared with the legacy auto-sweep engine.

    Kept identical so the existing ``/api/me/auto-sweep/status`` endpoint
    keeps returning the same payload shape.
    """
    return f"auto_sweep:{user_id}"


def _status_value(status: Any) -> str:
    return status.value if hasattr(status, "value") else status


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
    reconcile_interval_sec: int = 300

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

    The cycle loop processes every user with ``auto_sweep_enabled=true``
    and rebalances either a single master account (legacy topology) or
    each active sub wallet (topology after onboarding).
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        client_factory: BinanceClientFactory | None = None,
        poll_interval_sec: int = _DEFAULT_POLL_INTERVAL_SEC,
        policy: RoutingPolicy | None = None,
        reconciler: WalletReconciler | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._client_factory = client_factory or get_client_factory()
        self._poll_interval_sec = poll_interval_sec
        self._policy = policy or RoutingPolicy()
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._reconciler = reconciler or get_wallet_reconciler(
            session_maker=session_maker,
            client_factory=self._client_factory,
            min_interval_sec=self._policy.reconcile_interval_sec,
        )
        self._last_reconcile_at: dict[str, datetime] = {}

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
        """One sweep pass across every user with auto-sweep enabled.

        Absorbs the legacy ``auto_sweep_engine`` loop. Per user, isolates
        failures via try/except so one bad account never blocks the rest.
        """
        async with self._session_maker() as session:
            users = await list_auto_sweep_enabled_users(session)
        if not users:
            return
        _log.info("CapitalRouter cycle: %d user(s)", len(users))
        for user in users:
            try:
                await self.process_user(user)
            except Exception as exc:  # noqa: BLE001
                _log.exception("router cycle failed for user=%s: %s", user.user_id, exc)
                await self._record_error(user.user_id, str(exc))

    async def process_user(self, user: UserProfile) -> None:
        """Run one cycle for a single user.

        Routes to either the legacy single-account flow or the sub-aware
        flow depending on whether sub wallets exist for this user. First
        runs the wallet reconciler (rate-limited per user) so the active
        sub list reflects whatever happened on Binance since the last
        pass.
        """
        await self._maybe_reconcile(user.user_id)

        async with self._session_maker() as session:
            wallets = await list_wallet_accounts(
                session, user_id=user.user_id, env="mainnet"
            )
        subs = [
            w
            for w in wallets
            if _role_value(w.role) == WalletRole.SUB.value
            and _status_value(w.status) == WalletAccountStatus.ACTIVE.value
        ]
        master = next(
            (w for w in wallets if _role_value(w.role) == WalletRole.MASTER.value),
            None,
        )

        if subs and master is not None:
            await self._process_user_with_subs(user, master=master, subs=subs)
        else:
            await self._process_user_legacy(user)

    # ── legacy single-account flow ────────────────────────────────

    async def _process_user_legacy(self, user: UserProfile) -> None:
        async with self._session_maker() as session:
            cred = await get_binance_credential(
                session, user_id=user.user_id, env="mainnet"
            )
        if not cred:
            await self._record_error(
                user.user_id, "Auto-sweep disabled: mainnet keys required"
            )
            return

        crypto = get_crypto_service()
        try:
            api_key = crypto.decrypt(cred.api_key_enc)
            api_secret = crypto.decrypt(cred.api_secret_enc)
        except Exception as exc:  # noqa: BLE001
            await self._record_error(
                user.user_id, f"Key decryption failed: {exc}"
            )
            return

        futures_buffer = float(user.auto_sweep_futures_buffer_usdt)
        sweep_threshold = float(user.auto_sweep_sweep_threshold_usdt)

        earn = BinanceEarnClient(api_key=api_key, api_secret=api_secret)
        try:
            futures_usdt = await earn.fetch_futures_available_balance()
            earn_usdt = 0.0
            try:
                earn_usdt = await earn.fetch_flexible_position_usdt()
            except BinanceEarnClientError as exc:
                _log.warning(
                    "flex position fetch failed user=%s: %s", user.user_id, exc
                )

            action, detail = await self._legacy_rebalance(
                earn,
                user_id=user.user_id,
                futures_usdt=futures_usdt,
                earn_usdt=earn_usdt,
                futures_buffer=futures_buffer,
                sweep_threshold=sweep_threshold,
            )
            await self._record_success(
                user.user_id,
                payload={
                    "topology": "master-only",
                    "futures_usdt": futures_usdt,
                    "earn_usdt": earn_usdt,
                    "futures_buffer_usdt": futures_buffer,
                    "sweep_threshold_usdt": sweep_threshold,
                    "last_action": action,
                    "detail": detail,
                },
            )
        finally:
            await earn.aclose()

    async def _legacy_rebalance(  # noqa: PLR0913 — kwargs collect distinct inputs for one decision
        self,
        earn: BinanceEarnClient,
        *,
        user_id: str,
        futures_usdt: float,
        earn_usdt: float,
        futures_buffer: float,
        sweep_threshold: float,
    ) -> tuple[str, dict[str, Any]]:
        action = "noop"
        detail: dict[str, Any] = {}

        if futures_usdt > futures_buffer + sweep_threshold:
            transfer_amount = futures_usdt - futures_buffer
            if transfer_amount >= _MIN_LEGACY_TXN_USDT:
                await earn.transfer_futures_to_spot(transfer_amount)
                _log.info(
                    "legacy: Futures→Spot user=%s amount=%.2f",
                    user_id, transfer_amount,
                )
                product_id = await earn.get_usdt_flexible_product_id()
                if not product_id:
                    raise BinanceEarnClientError(
                        "No USDT Flexible product available"
                    )
                detail = await earn.subscribe(transfer_amount, product_id)
                action = "subscribed"
        elif futures_usdt < futures_buffer and earn_usdt > 0:
            need = min(futures_buffer - futures_usdt, earn_usdt)
            if need >= _MIN_LEGACY_TXN_USDT:
                product_id = await earn.get_usdt_flexible_product_id()
                if not product_id:
                    raise BinanceEarnClientError(
                        "No USDT Flexible product available"
                    )
                detail = await earn.redeem(need, product_id)
                await earn.transfer_spot_to_futures(need)
                action = "redeemed"

        return action, detail

    # ── sub-aware flow ───────────────────────────────────────────

    async def _process_user_with_subs(
        self,
        user: UserProfile,
        *,
        master: WalletAccount,
        subs: list[WalletAccount],
    ) -> None:
        """Per-sub rebalance via master's sub-account API + universal_transfer.

        For each sub:

        * Query the sub's Futures available USDT via the master key.
        * If ``available > buffer + threshold`` → sweep excess
          ``sub futures → master spot`` via :meth:`transfer`.
        * If ``available < buffer`` → top up master spot → sub futures
          (after optionally redeeming SimpleEarn on the master side).

        After per-sub rebalances, parks any excess master spot in
        SimpleEarn so idle capital still yields.
        """
        try:
            async with self._session_maker() as bootstrap_session:
                sub_client = await self._client_factory.get_master_subaccount_client(
                    bootstrap_session,
                    user_id=user.user_id,
                    env="mainnet",
                )
        except BinanceClientFactoryError as exc:
            await self._record_error(user.user_id, f"master client unavailable: {exc}")
            return

        sub_results: list[dict[str, Any]] = []
        topup_needed: list[tuple[WalletAccount, float]] = []
        sweep_amount_total = 0.0

        for sub in subs:
            try:
                avail = await self._fetch_sub_futures_available(sub_client, sub)
            except (BinanceSubAccountClientError, httpx.HTTPError) as exc:
                _log.warning(
                    "futures balance fetch failed user=%s sub=%s: %s",
                    user.user_id, sub.alias, exc,
                )
                sub_results.append(
                    {"alias": sub.alias, "error": str(exc), "action": "skipped"}
                )
                continue

            buffer = float(user.auto_sweep_futures_buffer_usdt)
            threshold = float(user.auto_sweep_sweep_threshold_usdt)

            entry: dict[str, Any] = {
                "alias": sub.alias,
                "futures_usdt": avail,
                "buffer_usdt": buffer,
                "threshold_usdt": threshold,
                "action": "noop",
            }

            if avail > buffer + threshold:
                excess = avail - buffer
                if excess >= float(self._policy.min_transfer_usdt):
                    moved = await self._safe_transfer(
                        user_id=user.user_id,
                        from_wallet=sub,
                        to_wallet=master,
                        from_type="USDT_FUTURE",
                        to_type="SPOT",
                        amount=excess,
                        reason="autosweep_sub_to_master",
                    )
                    if moved:
                        entry["action"] = "swept_to_master"
                        entry["amount"] = float(excess)
                        sweep_amount_total += float(excess)
                    else:
                        entry["action"] = "sweep_failed"
            elif avail < buffer:
                need = buffer - avail
                if need >= float(self._policy.min_transfer_usdt):
                    topup_needed.append((sub, need))
                    entry["action"] = "pending_topup"
                    entry["needed_usdt"] = float(need)

            sub_results.append(entry)

        await self._handle_master_earn(
            user,
            master=master,
            topups=topup_needed,
            sweep_amount=sweep_amount_total,
            sub_results=sub_results,
        )

    async def _handle_master_earn(  # noqa: PLR0913, PLR0912 — single-shot cycle aggregation
        self,
        user: UserProfile,
        *,
        master: WalletAccount,
        topups: list[tuple[WalletAccount, float]],
        sweep_amount: float,
        sub_results: list[dict[str, Any]],
    ) -> None:
        """Handle SimpleEarn subscribe/redeem + master→sub topups."""
        api_key, api_secret = await self._decrypt_wallet_keys(master)
        if api_key is None or api_secret is None:
            await self._record_error(
                user.user_id, "master key decryption failed; skipping earn step"
            )
            return

        earn = BinanceEarnClient(api_key=api_key, api_secret=api_secret)
        master_action: dict[str, Any] = {"subscribed": 0.0, "redeemed": 0.0}
        try:
            # Top-ups first: redeem from Earn if master spot is short.
            total_topup = sum(amount for _, amount in topups)
            if total_topup > 0:
                master_spot = await earn.fetch_spot_usdt_balance()
                if master_spot < total_topup:
                    earn_pos = 0.0
                    with contextlib.suppress(BinanceEarnClientError):
                        earn_pos = await earn.fetch_flexible_position_usdt()
                    need = total_topup - master_spot
                    if earn_pos > 0 and need > 0:
                        product_id = await earn.get_usdt_flexible_product_id()
                        if product_id:
                            redeem_amount = min(need, earn_pos)
                            await earn.redeem(redeem_amount, product_id)
                            master_action["redeemed"] = float(redeem_amount)

                for sub, amount in topups:
                    moved = await self._safe_transfer(
                        user_id=user.user_id,
                        from_wallet=master,
                        to_wallet=sub,
                        from_type="SPOT",
                        to_type="USDT_FUTURE",
                        amount=amount,
                        reason="autosweep_master_to_sub",
                    )
                    for entry in sub_results:
                        if entry.get("alias") == sub.alias and entry.get(
                            "action"
                        ) == "pending_topup":
                            entry["action"] = (
                                "topped_up" if moved else "topup_failed"
                            )

            # Park sweep proceeds in SimpleEarn.
            if sweep_amount >= float(self._policy.earn_min_subscribe_usdt):
                product_id = await earn.get_usdt_flexible_product_id()
                if product_id:
                    try:
                        await earn.subscribe(sweep_amount, product_id)
                        master_action["subscribed"] = float(sweep_amount)
                    except BinanceEarnClientError as exc:
                        _log.warning(
                            "earn subscribe failed user=%s amount=%.2f: %s",
                            user.user_id, sweep_amount, exc,
                        )
        finally:
            await earn.aclose()

        await self._record_success(
            user.user_id,
            payload={
                "topology": "sub-aware",
                "subs": sub_results,
                "master_earn": master_action,
                "sweep_amount_usdt": sweep_amount,
            },
        )

    async def _fetch_sub_futures_available(
        self,
        sub_client: BinanceSubAccountClient,
        sub: WalletAccount,
    ) -> float:
        raw = await sub_client.get_sub_futures_account(
            email=sub.sub_account_email
        )
        # V2 sub-account/futures/account nests under futureAccountResp
        # (USD-M) or deliveryAccountResp (COIN-M).
        data: dict[str, Any] = raw if isinstance(raw, dict) else {}
        for nested_key in ("futureAccountResp", "deliveryAccountResp"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                data = nested
                break
        for key in ("availableBalance", "totalMarginBalance", "totalWalletBalance"):
            value = data.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        assets = data.get("assets")
        if isinstance(assets, list):
            for entry in assets:
                if isinstance(entry, dict) and entry.get("asset") == "USDT":
                    for k in ("availableBalance", "walletBalance"):
                        v = entry.get(k)
                        if v is not None:
                            try:
                                return float(v)
                            except (TypeError, ValueError):
                                continue
        return 0.0

    async def _decrypt_wallet_keys(
        self, wallet: WalletAccount
    ) -> tuple[str | None, str | None]:
        if not wallet.api_key_enc or not wallet.api_secret_enc:
            return None, None
        try:
            crypto = get_crypto_service()
            return crypto.decrypt(wallet.api_key_enc), crypto.decrypt(
                wallet.api_secret_enc
            )
        except Exception:  # noqa: BLE001
            return None, None

    async def _safe_transfer(  # noqa: PLR0913 — gateway to the audited transfer
        self,
        *,
        user_id: str,
        from_wallet: WalletAccount,
        to_wallet: WalletAccount,
        from_type: WalletType,
        to_type: WalletType,
        amount: float,
        reason: str,
    ) -> bool:
        """Wrap :meth:`transfer` and swallow non-fatal errors.

        Returns True on success, False on failure (after logging).
        """
        async with self._session_maker() as session:
            try:
                await self.transfer(
                    session,
                    user_id=user_id,
                    env="mainnet",
                    from_wallet_account_id=(
                        None
                        if _role_value(from_wallet.role) == WalletRole.MASTER.value
                        else str(from_wallet.id)
                    ),
                    to_wallet_account_id=(
                        None
                        if _role_value(to_wallet.role) == WalletRole.MASTER.value
                        else str(to_wallet.id)
                    ),
                    from_wallet_type=from_type,
                    to_wallet_type=to_type,
                    asset="USDT",
                    amount=Decimal(str(amount)),
                    reason=reason,
                )
                return True
            except CapitalRouterError as exc:
                _log.warning(
                    "auto-sweep transfer failed user=%s reason=%s: %s",
                    user_id, reason, exc,
                )
                return False

    # ── snapshot persistence (same key as legacy engine) ─────────

    async def _record_success(
        self, user_id: str, *, payload: dict[str, Any]
    ) -> None:
        body = {
            "last_run_at": datetime.now(UTC).isoformat(),
            "last_action": payload.get("last_action", "completed"),
            "last_error": None,
        }
        body.update(payload)
        async with self._session_maker() as session:
            await upsert_account_snapshot(
                session, key=_snapshot_key(user_id), data_json=body
            )
            await session.commit()

    async def _maybe_reconcile(self, user_id: str) -> ReconcileSummary | None:
        """Run the wallet reconciler, but no more than once per interval."""
        interval = max(0, int(self._policy.reconcile_interval_sec))
        now = datetime.now(UTC)
        last = self._last_reconcile_at.get(user_id)
        if interval > 0 and last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < interval:
                return None
        try:
            summary = await self._reconciler.reconcile_user(user_id=user_id)
        except Exception as exc:  # noqa: BLE001 — reconciler is best-effort
            _log.warning(
                "reconciler raised for user=%s; continuing cycle: %s",
                user_id,
                exc,
            )
            return None
        self._last_reconcile_at[user_id] = now
        if summary.error:
            _log.info(
                "reconcile soft-failed user=%s: %s", user_id, summary.error
            )
        elif (
            summary.marked_missing
            or summary.marked_disabled
            or summary.cleared_missing
            or summary.unmanaged_binance_subs
        ):
            _log.info(
                "reconcile user=%s: missing=%d disabled=%d cleared=%d "
                "unmanaged=%d",
                user_id,
                len(summary.marked_missing),
                len(summary.marked_disabled),
                len(summary.cleared_missing),
                len(summary.unmanaged_binance_subs),
            )
        return summary

    async def _record_error(self, user_id: str, message: str) -> None:
        async with self._session_maker() as session:
            existing = await get_account_snapshot(
                session, key=_snapshot_key(user_id)
            )
            base = dict(existing.data_json) if existing and existing.data_json else {}
            base.update(
                {
                    "last_run_at": datetime.now(UTC).isoformat(),
                    "last_action": "error",
                    "last_error": message,
                }
            )
            await upsert_account_snapshot(
                session, key=_snapshot_key(user_id), data_json=base
            )
            await session.commit()

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


# ── one-shot helpers (backward compatible with auto_sweep_engine) ─────


async def trigger_user_cycle(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
) -> None:
    """Run a single router cycle immediately for one user.

    Used by the API right after a user enables auto-sweep so the change
    takes effect without waiting for the next polling cycle. Failures
    are isolated and persisted to the per-user snapshot instead of being
    Failures are isolated and persisted to the per-user snapshot instead
    of being raised to the caller.
    """
    router = get_capital_router(session_maker)
    try:
        async with session_maker() as session:
            user = await get_user_profile(session, user_id=user_id)
        if user is None or not user.auto_sweep_enabled:
            return
        await router.process_user(user)
    except Exception as exc:  # noqa: BLE001
        _log.exception("immediate router cycle failed for user=%s: %s", user_id, exc)
        with contextlib.suppress(Exception):
            await router._record_error(user_id, str(exc))  # noqa: SLF001 — same module helper


async def get_user_status(
    session: AsyncSession, *, user_id: str
) -> dict[str, Any] | None:
    """Return the last router cycle snapshot for ``user_id``.

    Backwards compatible with ``auto_sweep_engine.get_user_status`` —
    same snapshot key, same payload shape (plus extra fields for the
    new sub-aware topology).
    """
    snap = await get_account_snapshot(session, key=_snapshot_key(user_id))
    if not snap:
        return None
    return snap.data_json
