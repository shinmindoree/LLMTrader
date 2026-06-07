"""Wallet Reconciler — Binance sub-account list ↔ DB sync.

Binance does **not** expose a sub-account delete API, nor does it push
state changes via webhook. To keep ``wallet_accounts`` honest, we
periodically pull the master's view of the sub-account roster and
reconcile against the DB:

* DB sub *not* present in Binance list → ``status='binance_missing'``
* DB sub present + ``isFreeze=true`` → ``status='disabled'``
* DB sub present + ``isFreeze=false`` → leave status untouched
  (don't fight the operator if they marked it ``disabled`` on purpose;
  only auto-recover when keys re-verify)
* Binance sub *not* present in DB → record in
  ``unmanaged_binance_subs`` summary but **do not auto-create** —
  the user might own unrelated sub-accounts.

The summary is persisted to ``account_snapshots`` under the key
``wallet_reconcile:<user_id>`` so the UI can render "last sync 3m ago"
and the operator can spot drift before it bites.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from binance.client_factory import (
    BinanceClientFactory,
    BinanceClientFactoryError,
    get_client_factory,
)
from binance.subaccount_client import BinanceSubAccountClientError
from control.models import (
    WalletAccount,
    WalletAccountStatus,
    WalletRole,
)
from control.repo import (
    get_account_snapshot,
    list_wallet_accounts,
    update_wallet_account_status,
    upsert_account_snapshot,
)

_log = logging.getLogger("llmtrader.wallet_reconciler")

_RECON_SNAPSHOT_KEY_FMT = "wallet_reconcile:{user_id}"


def _role_value(role: Any) -> str:
    return role.value if hasattr(role, "value") else role


def _status_value(status: Any) -> str:
    return status.value if hasattr(status, "value") else status


def reconcile_snapshot_key(user_id: str) -> str:
    return _RECON_SNAPSHOT_KEY_FMT.format(user_id=user_id)


@dataclass(slots=True)
class ReconcileSummary:
    """Outcome of one reconcile pass for a single user/env pair."""

    user_id: str
    env: str
    ok: bool
    ts: str
    binance_subs: int = 0
    db_subs: int = 0
    marked_missing: list[str] = field(default_factory=list)
    marked_disabled: list[str] = field(default_factory=list)
    cleared_missing: list[str] = field(default_factory=list)
    unmanaged_binance_subs: list[str] = field(default_factory=list)
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class WalletReconciler:
    """Pull Binance sub-account list and reconcile ``wallet_accounts``.

    Designed to be called either:

    * by ``CapitalRouter.process_user`` once every ``min_interval_sec``,
    * or manually via ``POST /api/me/wallets/sync``.

    The reconciler is **idempotent** and **never throws to the caller** —
    failures land in the summary's ``error`` field instead so a slow
    Binance shouldn't bring down the router cycle.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        client_factory: BinanceClientFactory | None = None,
        min_interval_sec: int = 300,
    ) -> None:
        self._session_maker = session_maker
        self._client_factory = client_factory or get_client_factory()
        self._min_interval_sec = min_interval_sec

    # ── public API ────────────────────────────────────────────────

    async def reconcile_user(
        self, *, user_id: str, env: str = "mainnet"
    ) -> ReconcileSummary:
        """Run one reconcile pass for a (user, env) pair."""
        ts = datetime.now(UTC).isoformat()
        summary = ReconcileSummary(user_id=user_id, env=env, ok=False, ts=ts)

        async with self._session_maker() as session:
            try:
                master_client = await self._client_factory.get_master_subaccount_client(
                    session, user_id=user_id, env=env
                )
            except BinanceClientFactoryError as exc:
                summary.error = f"no master client: {exc}"
                await self._persist_summary(session, summary)
                return summary

            try:
                rows = await master_client.list_subaccounts()
            except BinanceSubAccountClientError as exc:
                summary.error = f"binance list failed: {exc}"
                await self._persist_summary(session, summary)
                return summary
            except Exception as exc:  # noqa: BLE001 — defensive
                summary.error = f"unexpected error: {exc}"
                await self._persist_summary(session, summary)
                return summary

            binance_by_email: dict[str, dict[str, Any]] = {}
            for r in rows:
                email = str(r.get("email") or "").strip().lower()
                if email:
                    binance_by_email[email] = r
            summary.binance_subs = len(binance_by_email)

            db_subs = [
                w
                for w in await list_wallet_accounts(
                    session, user_id=user_id, env=env
                )
                if _role_value(w.role) == WalletRole.SUB.value
            ]
            summary.db_subs = len(db_subs)

            db_emails = {
                (w.sub_account_email or "").strip().lower()
                for w in db_subs
                if w.sub_account_email
            }

            for w in db_subs:
                await self._reconcile_one(session, w, binance_by_email, summary)

            for email in sorted(set(binance_by_email) - db_emails):
                summary.unmanaged_binance_subs.append(email)

            await self._persist_summary(session, summary)
            await session.commit()

        summary.ok = summary.error is None
        return summary

    async def get_last_summary(
        self, *, user_id: str
    ) -> dict[str, Any] | None:
        """Return the most recent reconcile summary payload for the user."""
        async with self._session_maker() as session:
            snapshot = await get_account_snapshot(
                session, key=reconcile_snapshot_key(user_id)
            )
        if snapshot is None:
            return None
        payload = snapshot.data_json
        if isinstance(payload, dict):
            return payload
        return None

    # ── internals ────────────────────────────────────────────────

    async def _reconcile_one(
        self,
        session: AsyncSession,
        wallet: WalletAccount,
        binance_by_email: dict[str, dict[str, Any]],
        summary: ReconcileSummary,
    ) -> None:
        """Update one DB sub's status based on the Binance row (if any)."""
        email = (wallet.sub_account_email or "").strip().lower()
        if not email:
            return  # cannot match; nothing to do

        current_status = _status_value(wallet.status)
        b_row = binance_by_email.get(email)

        if b_row is None:
            # Disappeared on Binance side (frozen-and-hidden, or wrong master)
            if current_status != WalletAccountStatus.BINANCE_MISSING.value:
                await update_wallet_account_status(
                    session,
                    wallet_account_id=wallet.id,
                    status=WalletAccountStatus.BINANCE_MISSING,
                )
                summary.marked_missing.append(email)
            return

        is_freeze = bool(b_row.get("isFreeze"))
        if is_freeze:
            if current_status != WalletAccountStatus.DISABLED.value:
                await update_wallet_account_status(
                    session,
                    wallet_account_id=wallet.id,
                    status=WalletAccountStatus.DISABLED,
                )
                summary.marked_disabled.append(email)
            return

        # Visible & not frozen: clear stale ``binance_missing`` only,
        # leave operator-driven states (``disabled``, ``key_missing``,
        # ``key_invalid``) alone.
        if current_status == WalletAccountStatus.BINANCE_MISSING.value:
            new_status = (
                WalletAccountStatus.ACTIVE
                if wallet.api_key_enc
                else WalletAccountStatus.KEY_MISSING
            )
            await update_wallet_account_status(
                session,
                wallet_account_id=wallet.id,
                status=new_status,
            )
            summary.cleared_missing.append(email)

    async def _persist_summary(
        self, session: AsyncSession, summary: ReconcileSummary
    ) -> None:
        with contextlib.suppress(Exception):
            await upsert_account_snapshot(
                session,
                key=reconcile_snapshot_key(summary.user_id),
                data_json=summary.to_payload(),
            )


# ── module-level singleton ────────────────────────────────────────────

_reconciler: WalletReconciler | None = None


def get_wallet_reconciler(
    *,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    client_factory: BinanceClientFactory | None = None,
    min_interval_sec: int = 300,
) -> WalletReconciler:
    """Return (and lazily create) the process-wide reconciler.

    The first caller wins on construction args; subsequent calls return
    the cached instance regardless of what they pass.
    """
    global _reconciler  # noqa: PLW0603 — module-level singleton accessor
    if _reconciler is None:
        if session_maker is None:
            raise RuntimeError(
                "WalletReconciler not initialised; first call must "
                "provide session_maker"
            )
        _reconciler = WalletReconciler(
            session_maker=session_maker,
            client_factory=client_factory,
            min_interval_sec=min_interval_sec,
        )
    return _reconciler


def reset_wallet_reconciler() -> None:
    """Drop the singleton (used by tests and graceful shutdown)."""
    global _reconciler  # noqa: PLW0603 — module-level singleton accessor
    _reconciler = None
