"""Wallet topology REST routes.

Exposes the sub-account / strategy-allocation / wallet-transfer surface
that the onboarding UI and operator tooling drive. Routes are registered
via :func:`register_wallet_routes` from :mod:`api.main` to keep the route
declarations colocated with the rest of the FastAPI app while letting the
schema and handler bodies live in a dedicated module.

Auth model mirrors the existing per-user routes: every endpoint requires
``require_auth`` and scopes data to the authenticated ``user_id``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from binance.client_factory import BinanceClientFactoryError, get_client_factory
from binance.subaccount_client import VALID_WALLET_TYPES, BinanceSubAccountClientError
from common.crypto import get_crypto_service
from control.models import (
    AllocationMode,
    WalletAccount,
    WalletAccountStatus,
    WalletPurpose,
    WalletRole,
    WalletTransfer,
)
from control.repo import (
    create_wallet_account,
    delete_strategy_allocation,
    delete_wallet_account,
    get_master_wallet_account,
    get_strategy_allocation,
    get_wallet_account,
    list_wallet_accounts,
    list_wallet_transfers,
    update_wallet_account_keys,
    update_wallet_account_meta,
    update_wallet_account_status,
    upsert_strategy_allocation,
)
from live.wallet_reconciler import (
    ReconcileSummary,
    WalletReconciler,
    get_wallet_reconciler,
)

AuthDep = Callable[..., Awaitable[Any]]
SessionDep = Callable[..., Awaitable[AsyncSession]]

logger = logging.getLogger("llmtrader.api.wallets")


# ── schemas ──────────────────────────────────────────────────────────


def _mask_key(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


class WalletAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    env: str
    role: str
    purpose: str
    alias: str
    sub_account_email: str | None = None
    status: str
    api_key_masked: str | None = None
    enabled_wallets: dict[str, Any] | None = None
    ip_whitelist: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CreateSubAccountIn(BaseModel):
    alias: str = Field(..., min_length=1, max_length=64)
    purpose: WalletPurpose = WalletPurpose.GENERIC
    env: str = Field(default="mainnet", pattern="^(mainnet|testnet)$")
    enable_futures: bool = True
    enable_options: bool = False


class UpdateWalletKeysIn(BaseModel):
    api_key: str = Field(..., min_length=8)
    api_secret: str = Field(..., min_length=8)
    ip_whitelist: list[str] | None = None
    mark_active: bool = True


class UpdateWalletStatusIn(BaseModel):
    status: WalletAccountStatus


class UpdateWalletMetaIn(BaseModel):
    purpose: WalletPurpose | None = None
    enabled_wallets: dict[str, Any] | None = None
    ip_whitelist: list[str] | None = None


class AllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    wallet_account_id: str
    allocation_mode: str
    allocated_usdt: float
    reserved_usdt: float
    free_usdt: float
    max_drawdown_pct: float | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UpsertAllocationIn(BaseModel):
    wallet_account_id: str
    allocated_usdt: float = Field(..., gt=0)
    allocation_mode: AllocationMode = AllocationMode.FIXED_USDT
    max_drawdown_pct: float | None = Field(default=None, ge=0, le=1)


class WalletTransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    from_wallet_account_id: str | None = None
    to_wallet_account_id: str | None = None
    from_wallet_type: str
    to_wallet_type: str
    asset: str
    amount: float
    reason: str | None = None
    status: str
    client_tran_id: str | None = None
    binance_tran_id: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


# ── serialization helpers ────────────────────────────────────────────


def _wallet_to_out(wallet: WalletAccount) -> WalletAccountOut:
    crypto = get_crypto_service()
    masked = None
    if wallet.api_key_enc:
        try:
            masked = _mask_key(crypto.decrypt(wallet.api_key_enc))
        except Exception:  # noqa: BLE001
            masked = "***decryption_error***"
    role = wallet.role.value if hasattr(wallet.role, "value") else wallet.role
    purpose = wallet.purpose.value if hasattr(wallet.purpose, "value") else wallet.purpose
    status = wallet.status.value if hasattr(wallet.status, "value") else wallet.status
    return WalletAccountOut(
        id=str(wallet.id),
        env=wallet.env,
        role=role,
        purpose=purpose,
        alias=wallet.alias,
        sub_account_email=wallet.sub_account_email,
        status=status,
        api_key_masked=masked,
        enabled_wallets=wallet.enabled_wallets,
        ip_whitelist=wallet.ip_whitelist,
        created_at=wallet.created_at.isoformat() if wallet.created_at else None,
        updated_at=wallet.updated_at.isoformat() if wallet.updated_at else None,
    )


def _transfer_to_out(transfer: WalletTransfer) -> WalletTransferOut:
    status = transfer.status.value if hasattr(transfer.status, "value") else transfer.status
    return WalletTransferOut(
        id=str(transfer.id),
        from_wallet_account_id=(
            str(transfer.from_wallet_account_id)
            if transfer.from_wallet_account_id
            else None
        ),
        to_wallet_account_id=(
            str(transfer.to_wallet_account_id)
            if transfer.to_wallet_account_id
            else None
        ),
        from_wallet_type=transfer.from_wallet_type,
        to_wallet_type=transfer.to_wallet_type,
        asset=transfer.asset,
        amount=float(transfer.amount),
        reason=transfer.reason,
        status=status,
        client_tran_id=transfer.client_tran_id,
        binance_tran_id=transfer.binance_tran_id,
        error_message=transfer.error_message,
        created_at=transfer.created_at.isoformat() if transfer.created_at else None,
        completed_at=transfer.completed_at.isoformat() if transfer.completed_at else None,
    )


class WalletSyncSummaryOut(BaseModel):
    """Public payload mirror of :class:`ReconcileSummary`."""

    user_id: str
    env: str
    ok: bool
    ts: str
    binance_subs: int = 0
    db_subs: int = 0
    marked_missing: list[str] = Field(default_factory=list)
    marked_disabled: list[str] = Field(default_factory=list)
    cleared_missing: list[str] = Field(default_factory=list)
    unmanaged_binance_subs: list[str] = Field(default_factory=list)
    error: str | None = None


def _summary_to_out(summary: ReconcileSummary) -> WalletSyncSummaryOut:
    return WalletSyncSummaryOut(**summary.to_payload())


# ── route registration ──────────────────────────────────────────────


def register_wallet_routes(  # noqa: PLR0915 — single registration point for a coherent route group
    app: FastAPI,
    *,
    require_auth_dep: AuthDep,
    db_session_dep: SessionDep,
) -> None:
    """Attach all ``/api/me/wallets*`` and related routes onto ``app``."""

    _auth_param = Depends(require_auth_dep)
    _session_param = Depends(db_session_dep)

    @app.get("/api/me/wallets", response_model=list[WalletAccountOut])
    async def list_wallets(
        env: str | None = None,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> list[WalletAccountOut]:
        wallets = await list_wallet_accounts(
            session, user_id=user.user_id, env=env
        )
        return [_wallet_to_out(w) for w in wallets]

    @app.get("/api/me/wallets/{wallet_account_id}", response_model=WalletAccountOut)
    async def get_wallet(
        wallet_account_id: str,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> WalletAccountOut:
        wid = _parse_uuid(wallet_account_id, label="wallet_account_id")
        wallet = await get_wallet_account(session, wallet_account_id=wid)
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=404, detail="Wallet not found")
        return _wallet_to_out(wallet)

    @app.post(
        "/api/me/wallets/subaccounts",
        response_model=WalletAccountOut,
        status_code=201,
    )
    async def create_subaccount(
        body: CreateSubAccountIn,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> WalletAccountOut:
        master = await get_master_wallet_account(
            session, user_id=user.user_id, env=body.env
        )
        if master is None:
            raise HTTPException(
                status_code=400,
                detail="Master wallet not configured for this env; add it first.",
            )

        existing = [
            w
            for w in await list_wallet_accounts(
                session, user_id=user.user_id, env=body.env
            )
            if w.alias.lower() == body.alias.lower()
        ]
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Wallet alias '{body.alias}' already exists in {body.env}",
            )

        factory = get_client_factory()
        try:
            sub_client = await factory.get_master_subaccount_client(
                session, user_id=user.user_id, env=body.env
            )
        except BinanceClientFactoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            email = await sub_client.create_virtual_subaccount(alias_string=body.alias)
            if body.enable_futures:
                await sub_client.enable_futures(email)
            if body.enable_options:
                await sub_client.enable_options(email)
        except BinanceSubAccountClientError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Binance sub-account API rejected request: {exc}",
            ) from exc

        wallet = await create_wallet_account(
            session,
            user_id=user.user_id,
            env=body.env,
            role=WalletRole.SUB,
            alias=body.alias,
            purpose=body.purpose.value,
            sub_account_email=email,
            api_key_enc=None,
            api_secret_enc=None,
            status=WalletAccountStatus.KEY_MISSING,
            enabled_wallets={
                "futures_um": body.enable_futures,
                "options": body.enable_options,
                "spot": True,
            },
            ip_whitelist=[],
        )
        await session.commit()
        return _wallet_to_out(wallet)

    @app.put(
        "/api/me/wallets/{wallet_account_id}/keys",
        response_model=WalletAccountOut,
    )
    async def update_wallet_keys(
        wallet_account_id: str,
        body: UpdateWalletKeysIn,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> WalletAccountOut:
        wid = _parse_uuid(wallet_account_id, label="wallet_account_id")
        wallet = await get_wallet_account(session, wallet_account_id=wid)
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=404, detail="Wallet not found")

        crypto = get_crypto_service()
        api_key_enc = crypto.encrypt(body.api_key)
        api_secret_enc = crypto.encrypt(body.api_secret)

        await update_wallet_account_keys(
            session,
            wallet_account_id=wid,
            api_key_enc=api_key_enc,
            api_secret_enc=api_secret_enc,
            mark_active=body.mark_active,
        )
        if body.ip_whitelist is not None:
            await update_wallet_account_meta(
                session,
                wallet_account_id=wid,
                ip_whitelist=body.ip_whitelist,
            )

        # Best-effort: push the IP whitelist to Binance for sub-account trading keys.
        # Failure here doesn't roll back — the operator can retry from the UI.
        if body.ip_whitelist and wallet.sub_account_email:
            try:
                factory = get_client_factory()
                master_client = await factory.get_master_subaccount_client(
                    session, user_id=user.user_id, env=wallet.env
                )
                await master_client.add_ip_restriction(
                    email=wallet.sub_account_email,
                    sub_api_key=body.api_key,
                    ip_addresses=body.ip_whitelist,
                )
            except (BinanceClientFactoryError, BinanceSubAccountClientError) as exc:
                logger.warning(
                    "IP whitelist push to Binance failed for wallet %s: %s",
                    wid,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort; never 500 from here
                logger.exception(
                    "Unexpected error pushing IP whitelist for wallet %s: %s",
                    wid,
                    exc,
                )

        await _factory_invalidate(wallet_account_id)
        await session.commit()
        refreshed = await get_wallet_account(session, wallet_account_id=wid)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Wallet not found")
        return _wallet_to_out(refreshed)

    @app.put(
        "/api/me/wallets/{wallet_account_id}/status",
        response_model=WalletAccountOut,
    )
    async def update_wallet_status(
        wallet_account_id: str,
        body: UpdateWalletStatusIn,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> WalletAccountOut:
        wid = _parse_uuid(wallet_account_id, label="wallet_account_id")
        wallet = await get_wallet_account(session, wallet_account_id=wid)
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=404, detail="Wallet not found")
        await update_wallet_account_status(
            session,
            wallet_account_id=wid,
            status=body.status,
        )
        await _factory_invalidate(wallet_account_id)
        await session.commit()
        refreshed = await get_wallet_account(session, wallet_account_id=wid)
        return _wallet_to_out(refreshed) if refreshed else _wallet_to_out(wallet)

    @app.put(
        "/api/me/wallets/{wallet_account_id}/meta",
        response_model=WalletAccountOut,
    )
    async def update_wallet_meta(
        wallet_account_id: str,
        body: UpdateWalletMetaIn,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> WalletAccountOut:
        wid = _parse_uuid(wallet_account_id, label="wallet_account_id")
        wallet = await get_wallet_account(session, wallet_account_id=wid)
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=404, detail="Wallet not found")
        await update_wallet_account_meta(
            session,
            wallet_account_id=wid,
            purpose=body.purpose.value if body.purpose else None,
            enabled_wallets=body.enabled_wallets,
            ip_whitelist=body.ip_whitelist,
        )
        await session.commit()
        refreshed = await get_wallet_account(session, wallet_account_id=wid)
        return _wallet_to_out(refreshed) if refreshed else _wallet_to_out(wallet)

    @app.delete("/api/me/wallets/{wallet_account_id}", status_code=204)
    async def delete_wallet(
        wallet_account_id: str,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> None:
        wid = _parse_uuid(wallet_account_id, label="wallet_account_id")
        wallet = await get_wallet_account(session, wallet_account_id=wid)
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=404, detail="Wallet not found")
        await delete_wallet_account(session, wallet_account_id=wid)
        await _factory_invalidate(wallet_account_id)
        await session.commit()

    # ── reconciler / drift sync ──────────────────────────────────

    @app.post(
        "/api/me/wallets/sync",
        response_model=WalletSyncSummaryOut,
    )
    async def sync_wallets(
        env: str = "mainnet",
        user: Any = _auth_param,
    ) -> WalletSyncSummaryOut:
        """Force a wallet reconcile against Binance for the current user."""
        try:
            reconciler: WalletReconciler = get_wallet_reconciler()
        except RuntimeError as exc:
            logger.warning("reconciler not initialised: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="wallet reconciler not ready",
            ) from exc
        summary = await reconciler.reconcile_user(
            user_id=user.user_id, env=env
        )
        return _summary_to_out(summary)

    @app.get(
        "/api/me/wallets/sync/status",
        response_model=WalletSyncSummaryOut | None,
    )
    async def get_sync_status(
        user: Any = _auth_param,
    ) -> WalletSyncSummaryOut | None:
        """Return the most recent reconcile summary, if any."""
        try:
            reconciler = get_wallet_reconciler()
        except RuntimeError:
            return None
        payload = await reconciler.get_last_summary(user_id=user.user_id)
        if payload is None:
            return None
        try:
            return WalletSyncSummaryOut(**payload)
        except Exception as exc:  # noqa: BLE001 — defensive: schema drift
            logger.warning("stale sync snapshot for %s: %s", user.user_id, exc)
            return None

    # ── strategy allocations ─────────────────────────────────────

    @app.get(
        "/api/me/jobs/{job_id}/allocation",
        response_model=AllocationOut | None,
    )
    async def get_allocation(
        job_id: str,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> AllocationOut | None:
        jid = _parse_uuid(job_id, label="job_id")
        alloc = await get_strategy_allocation(session, job_id=jid)
        if alloc is None:
            return None
        wallet = await get_wallet_account(
            session, wallet_account_id=alloc.wallet_account_id
        )
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="not your allocation")
        return _allocation_to_out(alloc)

    @app.put(
        "/api/me/jobs/{job_id}/allocation",
        response_model=AllocationOut,
    )
    async def upsert_allocation(
        job_id: str,
        body: UpsertAllocationIn,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> AllocationOut:
        jid = _parse_uuid(job_id, label="job_id")
        wid = _parse_uuid(body.wallet_account_id, label="wallet_account_id")
        wallet = await get_wallet_account(session, wallet_account_id=wid)
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="not your wallet")
        alloc = await upsert_strategy_allocation(
            session,
            job_id=jid,
            wallet_account_id=wid,
            allocated_usdt=body.allocated_usdt,
            allocation_mode=body.allocation_mode,
            max_drawdown_pct=body.max_drawdown_pct,
        )
        await session.commit()
        return _allocation_to_out(alloc)

    @app.delete("/api/me/jobs/{job_id}/allocation", status_code=204)
    async def delete_allocation(
        job_id: str,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> None:
        jid = _parse_uuid(job_id, label="job_id")
        alloc = await get_strategy_allocation(session, job_id=jid)
        if alloc is None:
            return
        wallet = await get_wallet_account(
            session, wallet_account_id=alloc.wallet_account_id
        )
        if wallet is None or wallet.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="not your allocation")
        await delete_strategy_allocation(session, job_id=jid)
        await session.commit()

    # ── wallet transfers (audit log) ─────────────────────────────

    @app.get(
        "/api/me/wallet-transfers",
        response_model=list[WalletTransferOut],
    )
    async def list_transfers(
        limit: int = 50,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> list[WalletTransferOut]:
        limit = max(1, min(int(limit), 500))
        transfers = await list_wallet_transfers(
            session, user_id=user.user_id, limit=limit
        )
        return [_transfer_to_out(t) for t in transfers]


def _parse_uuid(value: str, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid {label}") from exc


def _allocation_to_out(alloc: Any) -> AllocationOut:
    allocated = float(alloc.allocated_usdt)
    reserved = float(alloc.reserved_usdt)
    mode = (
        alloc.allocation_mode.value
        if hasattr(alloc.allocation_mode, "value")
        else alloc.allocation_mode
    )
    return AllocationOut(
        job_id=str(alloc.job_id),
        wallet_account_id=str(alloc.wallet_account_id),
        allocation_mode=mode,
        allocated_usdt=allocated,
        reserved_usdt=reserved,
        free_usdt=max(0.0, allocated - reserved),
        max_drawdown_pct=(
            float(alloc.max_drawdown_pct)
            if alloc.max_drawdown_pct is not None
            else None
        ),
        created_at=alloc.created_at.isoformat() if alloc.created_at else None,
        updated_at=alloc.updated_at.isoformat() if alloc.updated_at else None,
    )


async def _factory_invalidate(wallet_account_id: str) -> None:
    """Drop any cached BinanceClientFactory entry for ``wallet_account_id``."""
    factory = get_client_factory()
    await factory.invalidate(wallet_account_id)


# Re-exported so callers in api.main can validate input enum / wallet type
# values without importing from the binance package directly.
__all__ = [
    "VALID_WALLET_TYPES",
    "Decimal",
    "register_wallet_routes",
]
