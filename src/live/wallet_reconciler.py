"""Wallet Reconciler — Binance sub-account list ↔ DB sync.

Binance does **not** expose a sub-account delete API, nor does it push
state changes via webhook. To keep ``wallet_accounts`` honest, we
periodically pull the master's view of the sub-account roster and
reconcile against the DB.

Since the new UX disallows creating sub-accounts from the app (the user
must do it on Binance), Binance is the **source of truth** for the
sub-account roster *and* their per-wallet enablement flags:

* DB sub *not* present in Binance list → ``status='binance_missing'``
* DB sub present + ``isFreeze=true`` → ``status='disabled'``
* DB sub present + ``isFreeze=false`` → leave operator-driven states
  (``disabled``, ``key_missing``, ``key_invalid``) alone; only clear
  stale ``binance_missing``.
* Binance sub *not* present in DB → **auto-INSERT** a row with
  ``alias`` derived from the email local-part, ``purpose='generic'``,
  ``status='key_missing'``. The user can rename / repurpose / supply
  keys later from the Sub-account detail page.

Additionally, the reconciler calls ``get_status()`` to mirror the
Binance-side wallet toggles (``isMarginEnabled`` / ``isFutureEnabled``)
into each DB row's ``enabled_wallets`` JSON. This lets the UI render
permission badges that match what the operator sees on Binance.

The summary is persisted to ``account_snapshots`` under the key
``wallet_reconcile:<user_id>`` so the UI can render "last sync 3m ago"
and the operator can spot drift before it bites.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from binance.client_factory import (
    BinanceClientFactory,
    BinanceClientFactoryError,
    get_client_factory,
)
from binance.options_client import (
    BinanceOptionsClient,
    BinanceOptionsClientError,
    resolve_options_base_url,
)
from binance.subaccount_client import BinanceSubAccountClientError
from common.crypto import get_crypto_service
from control.models import (
    WalletAccount,
    WalletAccountStatus,
    WalletPurpose,
    WalletRole,
)
from control.repo import (
    create_wallet_account,
    get_account_snapshot,
    list_wallet_accounts,
    update_wallet_account_meta,
    update_wallet_account_status,
    upsert_account_snapshot,
)

_log = logging.getLogger("llmtrader.wallet_reconciler")

_RECON_SNAPSHOT_KEY_FMT = "wallet_reconcile:{user_id}"

# Aliases collide on (user_id, env, alias), so we sanitise the email
# local-part to a safe slug, then append a numeric suffix if needed.
_ALIAS_SAFE_RE = re.compile(r"[^a-z0-9_-]+")
_ALIAS_MAX_LEN = 48  # leaves headroom for "-NN" disambiguation suffix


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
    auto_created: list[str] = field(default_factory=list)
    permissions_synced: list[str] = field(default_factory=list)
    # Retained for backward-compatibility with persisted snapshots that
    # were written before auto-create became the default. New runs will
    # leave it empty.
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

            # Best-effort permission snapshot — Binance returns one row
            # per sub from /sapi/v1/sub-account/status with the flags we
            # need (isFutureEnabled / isMarginEnabled). If it fails we
            # skip permission sync but keep the rest of the cycle going.
            status_by_email: dict[str, dict[str, Any]] = {}
            try:
                for s in await master_client.get_status():
                    se = str(s.get("email") or "").strip().lower()
                    if se:
                        status_by_email[se] = s
            except BinanceSubAccountClientError as exc:
                _log.warning(
                    "sub-account status fetch failed for user=%s env=%s: %s",
                    user_id,
                    env,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001 — defensive
                _log.warning(
                    "sub-account status fetch unexpected error user=%s env=%s: %s",
                    user_id,
                    env,
                    exc,
                )

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
            taken_aliases = {w.alias for w in db_subs}

            for w in db_subs:
                await self._reconcile_one(
                    session, w, binance_by_email, status_by_email, summary
                )

            for email in sorted(set(binance_by_email) - db_emails):
                created = await self._auto_create(
                    session,
                    user_id=user_id,
                    env=env,
                    email=email,
                    binance_row=binance_by_email[email],
                    status_row=status_by_email.get(email),
                    taken_aliases=taken_aliases,
                )
                if created is not None:
                    taken_aliases.add(created.alias)
                    summary.auto_created.append(email)

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
        status_by_email: dict[str, dict[str, Any]],
        summary: ReconcileSummary,
    ) -> None:
        """Update one DB sub's status + permissions based on Binance."""
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

        # Mirror Binance-side permission flags into enabled_wallets.
        status_row = status_by_email.get(email)
        synced = await self._sync_permissions(
            session,
            wallet=wallet,
            binance_row=b_row,
            status_row=status_row,
        )
        if synced:
            summary.permissions_synced.append(email)

    @staticmethod
    def _derive_permissions(
        binance_row: dict[str, Any] | None,
        status_row: dict[str, Any] | None,
    ) -> dict[str, bool]:
        """Project Binance flags into the ``enabled_wallets`` schema.

        Spot is always enabled on a sub-account; futures/margin reflect
        ``isFutureEnabled`` / ``isMarginEnabled`` from
        ``/sapi/v1/sub-account/status`` (preferred) or the ``list`` row
        as a fallback. Options is **not** reported by either endpoint, so
        the caller must probe it separately via the sub's own API key.
        """
        flags: dict[str, bool] = {"spot": True}
        src: dict[str, Any] = {}
        if status_row:
            src.update(status_row)
        if binance_row:
            for k, v in binance_row.items():
                src.setdefault(k, v)
        if "isFutureEnabled" in src:
            flags["futures_um"] = bool(src.get("isFutureEnabled"))
            flags["futures_cm"] = bool(src.get("isFutureEnabled"))
        if "isMarginEnabled" in src:
            flags["margin"] = bool(src.get("isMarginEnabled"))
        return flags

    async def _probe_options_enabled(
        self, wallet: WalletAccount
    ) -> bool | None:
        """Best-effort probe: can the sub's API key call ``/eapi/v1/marginAccount``?

        Returns ``True``/``False`` when the probe completes, or ``None`` when
        we cannot probe (missing/undecryptable sub key) — in which case the
        existing flag in the DB is preserved.
        """
        api_key_enc = getattr(wallet, "api_key_enc", None)
        api_secret_enc = getattr(wallet, "api_secret_enc", None)
        if not api_key_enc or not api_secret_enc:
            return None
        try:
            crypto = get_crypto_service()
            api_key = crypto.decrypt(api_key_enc)
            api_secret = crypto.decrypt(api_secret_enc)
        except Exception:  # noqa: BLE001
            return None
        env_str = (
            wallet.env.value if hasattr(wallet.env, "value") else wallet.env
        ) or "mainnet"
        client = BinanceOptionsClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=resolve_options_base_url(str(env_str)),
        )
        try:
            await client.fetch_account()
            return True
        except BinanceOptionsClientError as exc:
            # 401/403/404 from eapi mean the key cannot use Options (either
            # the underlying account never enabled European Options, or the
            # key lacks the Options permission). Treat all as ``False``.
            _log.debug(
                "options probe failed for %s: %s",
                wallet.sub_account_email,
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "options probe error for %s: %s",
                wallet.sub_account_email,
                exc,
            )
            return None
        finally:
            await client.aclose()

    async def _sync_permissions(
        self,
        session: AsyncSession,
        *,
        wallet: WalletAccount,
        binance_row: dict[str, Any] | None,
        status_row: dict[str, Any] | None,
    ) -> bool:
        derived = self._derive_permissions(binance_row, status_row)
        # Options is not exposed by /sub-account/status; probe via the
        # sub's own key so the badge matches reality. Only override the
        # existing value when the probe returned a definitive answer.
        options_enabled = await self._probe_options_enabled(wallet)
        if options_enabled is not None:
            derived["options"] = options_enabled
        if not derived:
            return False
        existing = dict(wallet.enabled_wallets or {})
        merged = {**existing, **derived}
        if merged == existing:
            return False
        await update_wallet_account_meta(
            session,
            wallet_account_id=wallet.id,
            enabled_wallets=merged,
        )
        # Reflect back on the in-memory row so downstream code in the
        # same cycle sees the new state.
        wallet.enabled_wallets = merged
        return True

    @staticmethod
    def _alias_from_email(email: str, taken: set[str]) -> str:
        """Derive a Binance-style alias from the email local-part.

        ``directional_001@xxx.local`` → ``directional_001``. If the
        slug collides with an existing alias for the same user/env we
        append ``-2``, ``-3``, … until unique.
        """
        local = email.split("@", 1)[0].lower() if "@" in email else email.lower()
        slug = _ALIAS_SAFE_RE.sub("-", local).strip("-_")
        if not slug:
            slug = "sub"
        slug = slug[:_ALIAS_MAX_LEN]
        if slug not in taken:
            return slug
        for n in range(2, 1000):
            candidate = f"{slug}-{n}"
            if candidate not in taken:
                return candidate
        # extreme fallback — extremely unlikely
        return f"{slug}-{datetime.now(UTC).strftime('%H%M%S')}"

    async def _auto_create(  # noqa: PLR0913 — each kwarg maps to a distinct column / Binance field
        self,
        session: AsyncSession,
        *,
        user_id: str,
        env: str,
        email: str,
        binance_row: dict[str, Any],
        status_row: dict[str, Any] | None,
        taken_aliases: set[str],
    ) -> WalletAccount | None:
        """Insert a DB row for a Binance-only sub-account.

        Binance is the source of truth; the app simply mirrors its
        roster. ``status`` starts as ``key_missing`` because the user
        still has to paste a sub-account API key from Binance before
        any trading can happen.
        """
        alias = self._alias_from_email(email, taken_aliases)
        permissions = self._derive_permissions(binance_row, status_row)
        is_freeze = bool(binance_row.get("isFreeze"))
        initial_status = (
            WalletAccountStatus.DISABLED
            if is_freeze
            else WalletAccountStatus.KEY_MISSING
        )
        try:
            return await create_wallet_account(
                session,
                user_id=user_id,
                env=env,
                role=WalletRole.SUB,
                alias=alias,
                purpose=WalletPurpose.GENERIC.value,
                sub_account_email=email,
                enabled_wallets=permissions,
                status=initial_status,
            )
        except Exception as exc:  # noqa: BLE001 — defensive; one bad row
            _log.warning(
                "auto-create failed for user=%s env=%s email=%s: %s",
                user_id,
                env,
                email,
                exc,
            )
            return None

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
