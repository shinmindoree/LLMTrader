"""Manual wallet-transfer REST routes (``/api/me/wallet-transfers/*``).

Powers the **"Binance Wallet 내부 이체"** tab in the web UI. The user picks
any (account, wallet-type) source and destination from a single form and
this module orchestrates the right Binance call(s):

* **Master internal** (e.g. Master Spot ↔ Master Options) →
  ``POST /sapi/v1/asset/transfer`` with a ``MAIN_*``/``*_MAIN`` type.
* **Master ↔ Sub / Sub ↔ Sub for non-Options wallets** →
  ``POST /sapi/v1/sub-account/universalTransfer`` (delegated to the
  existing :class:`CapitalRouter`).
* **Sub-internal Spot ↔ Options** → ``POST /sapi/v1/asset/transfer`` using
  the *sub's own* API key (the sub-account universal transfer endpoint
  does not support ``OPTION`` as a wallet type).
* **Cross-account with Options on either side** → decomposed into multiple
  legs (e.g. Master Opt → Master Spot → Sub Spot → Sub Opt is 3 legs).

Every leg writes a row to ``wallet_transfers`` with a distinct
``client_tran_id`` (idempotent on retry) and a ``reason`` of the form
``"manual"`` or ``"manual:leg2/3"`` so the UI can group them.

The companion endpoint ``GET /api/me/wallet-balances`` returns a flat
matrix of (account × wallet type → asset balance) so the UI can show a
balance grid and validate amounts before submit.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from binance.client_factory import (
    BinanceClientFactory,
    BinanceClientFactoryError,
    get_client_factory,
)
from binance.earn_client import BinanceEarnClient, BinanceEarnClientError
from binance.futures_balance import (
    BinanceFuturesBalanceError,
    fetch_cm_futures_account,
    fetch_um_futures_account,
)
from binance.options_client import (
    BinanceOptionsClient,
    BinanceOptionsClientError,
    resolve_options_base_url,
)
from binance.subaccount_client import (
    VALID_WALLET_TYPES,
    BinanceSubAccountClient,
    BinanceSubAccountClientError,
)
from common.crypto import get_crypto_service
from control.models import WalletAccount, WalletTransfer, WalletTransferStatus
from control.repo import (
    create_wallet_transfer,
    get_master_wallet_account,
    get_wallet_account,
    get_wallet_transfer_by_client_id,
    list_wallet_accounts,
    list_wallet_transfers,
    mark_wallet_transfer_failed,
    mark_wallet_transfer_succeeded,
)

AuthDep = Callable[..., Awaitable[Any]]
SessionDep = Callable[..., Awaitable[AsyncSession]]

logger = logging.getLogger("llmtrader.api.transfers")

# Wallet types we expose in the UI. ``OPTION`` and ``EARN_FLEXIBLE`` are
# *not* in :data:`VALID_WALLET_TYPES` because Binance's sub-account
# universal transfer rejects them — we handle them via dedicated paths
# (``/sapi/v1/asset/transfer`` for Options; ``/sapi/v1/simple-earn/*`` for
# Simple Earn Flexible).
UI_WALLET_TYPES: tuple[str, ...] = (
    "SPOT",
    "USDT_FUTURE",
    "COIN_FUTURE",
    "MARGIN",
    "OPTION",
    "EARN_FLEXIBLE",
)

# Wallet types that don't participate in Binance's universal-transfer
# graph and therefore have to be reached/left by funneling through SPOT.
NEEDS_FUNNEL_WALLETS: frozenset[str] = frozenset({"OPTION", "EARN_FLEXIBLE"})

# Simple Earn Flexible is currently USDT-only on our backend (the
# :class:`BinanceEarnClient` only knows the USDT product). Reject other
# assets early to avoid mid-plan failures.
EARN_SUPPORTED_ASSETS: frozenset[str] = frozenset({"USDT"})

# Supported assets in v1. Adding new symbols requires double-checking that
# the asset is enabled on every wallet type we support.
SUPPORTED_ASSETS: tuple[str, ...] = ("USDT", "USDC", "BTC", "ETH", "BNB")


# ─────────────────────────────────────────────────────────────────────
# schemas
# ─────────────────────────────────────────────────────────────────────


class WalletBalanceCell(BaseModel):
    """Single asset balance inside one (account, wallet) cell."""

    asset: str
    free: float
    locked: float = 0.0
    total: float


class WalletBalanceRow(BaseModel):
    """Per-account balance snapshot across all supported wallets."""

    wallet_account_id: str | None = None  # None = master
    role: str  # "master" | "sub"
    alias: str
    env: str
    email: str | None = None
    enabled_wallets: dict[str, Any] = Field(default_factory=dict)
    balances: dict[str, list[WalletBalanceCell]] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)


class WalletBalancesOut(BaseModel):
    ts: str
    rows: list[WalletBalanceRow]


class TransferIn(BaseModel):
    """Request body for ``POST /api/me/wallet-transfers``."""

    from_wallet_account_id: str | None = Field(
        default=None,
        description="UUID of the source wallet account. Omit/null for master.",
    )
    to_wallet_account_id: str | None = Field(
        default=None,
        description="UUID of the destination wallet account. Omit/null for master.",
    )
    from_wallet_type: str = Field(
        ..., description="One of SPOT / USDT_FUTURE / COIN_FUTURE / MARGIN / OPTION"
    )
    to_wallet_type: str = Field(...)
    asset: str = Field(default="USDT")
    amount: float = Field(..., gt=0)
    env: str = Field(default="mainnet", pattern="^(mainnet|testnet)$")


class TransferLegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    leg_index: int
    leg_total: int
    from_wallet_account_id: str | None = None
    to_wallet_account_id: str | None = None
    from_wallet_type: str
    to_wallet_type: str
    asset: str
    amount: float
    status: str
    binance_tran_id: str | None = None
    client_tran_id: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class TransferOut(BaseModel):
    """Aggregate result of a (possibly multi-leg) manual transfer."""

    ok: bool
    intent_id: str
    leg_total: int
    legs: list[TransferLegOut]
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────
# leg planner — turns a (from, to) intent into 1..3 atomic legs
# ─────────────────────────────────────────────────────────────────────


class _Endpoint:
    """Resolved source/destination of a transfer leg.

    ``account`` is ``None`` when the endpoint is the master account; for
    sub-accounts it is the loaded :class:`WalletAccount` row.
    """

    __slots__ = ("account", "wallet_type")

    def __init__(
        self,
        account: WalletAccount | None,
        wallet_type: str,
    ) -> None:
        self.account = account
        self.wallet_type = wallet_type

    @property
    def is_master(self) -> bool:
        return self.account is None

    @property
    def account_id(self) -> uuid.UUID | None:
        return self.account.id if self.account is not None else None

    @property
    def email(self) -> str | None:
        return self.account.sub_account_email if self.account is not None else None

    @property
    def label(self) -> str:
        prefix = "master" if self.is_master else (self.account.alias or "sub")  # type: ignore[union-attr]
        return f"{prefix}:{self.wallet_type}"


class _PlannedLeg:
    """One atomic Binance call within a transfer plan.

    ``kind`` decides which API the executor will hit:

    * ``"master_internal"`` — master ``/sapi/v1/asset/transfer``
    * ``"sub_universal"`` — master ``/sapi/v1/sub-account/universalTransfer``
    * ``"sub_internal"`` — sub key ``/sapi/v1/asset/transfer`` (e.g.
      Spot ↔ Options inside a single sub)
    * ``"earn_subscribe"`` — master key ``/sapi/v1/simple-earn/flexible/subscribe``
      (SPOT → EARN_FLEXIBLE on master)
    * ``"earn_redeem"`` — master key ``/sapi/v1/simple-earn/flexible/redeem``
      (EARN_FLEXIBLE → SPOT on master)
    * ``"sub_earn_subscribe"`` — sub key Simple-Earn subscribe (sub SPOT → sub EARN)
    * ``"sub_earn_redeem"`` — sub key Simple-Earn redeem (sub EARN → sub SPOT)
    """

    __slots__ = ("from_ep", "kind", "to_ep")

    def __init__(self, kind: str, from_ep: _Endpoint, to_ep: _Endpoint) -> None:
        self.kind = kind
        self.from_ep = from_ep
        self.to_ep = to_ep


def _funnel_out_kind(ep: _Endpoint) -> str:
    """Return the leg kind that moves funds from ``ep`` to its account's SPOT."""
    wt = ep.wallet_type
    is_master = ep.is_master
    if wt == "OPTION":
        return "master_internal" if is_master else "sub_internal"
    if wt == "EARN_FLEXIBLE":
        return "earn_redeem" if is_master else "sub_earn_redeem"
    raise ValueError(f"unexpected funnel-out wallet: {wt}")


def _funnel_in_kind(ep: _Endpoint) -> str:
    """Return the leg kind that moves funds from the account's SPOT into ``ep``."""
    wt = ep.wallet_type
    is_master = ep.is_master
    if wt == "OPTION":
        return "master_internal" if is_master else "sub_internal"
    if wt == "EARN_FLEXIBLE":
        return "earn_subscribe" if is_master else "sub_earn_subscribe"
    raise ValueError(f"unexpected funnel-in wallet: {wt}")


def _build_plan(  # noqa: PLR0912 — explicit decision tree is more readable than a graph search
    from_ep: _Endpoint, to_ep: _Endpoint
) -> list[_PlannedLeg]:
    """Decompose an intent into the minimum number of legs.

    The decision tree is purposely explicit (no graph search) — there are
    only a handful of cases and they map directly onto the rules in §11
    of ``plan.md``.

    OPTION and EARN_FLEXIBLE both sit outside Binance's universal-transfer
    graph and therefore route through SPOT.  We treat them uniformly via
    :func:`_funnel_in_kind` / :func:`_funnel_out_kind`.
    """
    f_master, t_master = from_ep.is_master, to_ep.is_master
    f_funnel = from_ep.wallet_type in NEEDS_FUNNEL_WALLETS
    t_funnel = to_ep.wallet_type in NEEDS_FUNNEL_WALLETS
    same_account = (
        from_ep.account_id == to_ep.account_id
    )  # both None (master) or same UUID

    # Identical endpoint guard (router will also catch, but earlier is nicer).
    if same_account and from_ep.wallet_type == to_ep.wallet_type:
        raise HTTPException(
            status_code=400, detail="source and destination are identical"
        )

    # Case 1: same account, neither side needs the SPOT funnel → 1 leg
    if same_account and not f_funnel and not t_funnel:
        if f_master:
            return [_PlannedLeg("master_internal", from_ep, to_ep)]
        # sub internal w/o funnel wallets: universalTransfer w/ from_email==to_email
        return [_PlannedLeg("sub_universal", from_ep, to_ep)]

    # Case 2: same account, at least one side needs the funnel.
    # Walk source → SPOT → destination, skipping the trivial hops.
    if same_account:
        legs: list[_PlannedLeg] = []
        spot = _Endpoint(from_ep.account, "SPOT")
        # leave source
        if f_funnel:
            legs.append(_PlannedLeg(_funnel_out_kind(from_ep), from_ep, spot))
        elif from_ep.wallet_type != "SPOT":
            # source is non-SPOT, non-funnel (e.g. USDT_FUTURE) and dst
            # is a funnel wallet → first hop is an asset/transfer
            kind = "master_internal" if f_master else "sub_internal"
            legs.append(_PlannedLeg(kind, from_ep, spot))
        # enter destination
        if t_funnel:
            legs.append(_PlannedLeg(_funnel_in_kind(to_ep), spot, to_ep))
        elif to_ep.wallet_type != "SPOT":
            kind = "master_internal" if t_master else "sub_internal"
            legs.append(_PlannedLeg(kind, spot, to_ep))
        return legs

    # Case 3: cross-account, neither side needs the funnel → universalTransfer 1 leg
    if not f_funnel and not t_funnel:
        return [_PlannedLeg("sub_universal", from_ep, to_ep)]

    # Case 4: cross-account, funnel involved → src → src.SPOT → dst.SPOT → dst
    legs = []
    src_spot = _Endpoint(from_ep.account, "SPOT")
    dst_spot = _Endpoint(to_ep.account, "SPOT")

    if f_funnel:
        legs.append(_PlannedLeg(_funnel_out_kind(from_ep), from_ep, src_spot))
    elif from_ep.wallet_type != "SPOT":
        kind = "master_internal" if f_master else "sub_internal"
        legs.append(_PlannedLeg(kind, from_ep, src_spot))

    legs.append(_PlannedLeg("sub_universal", src_spot, dst_spot))

    if t_funnel:
        legs.append(_PlannedLeg(_funnel_in_kind(to_ep), dst_spot, to_ep))
    elif to_ep.wallet_type != "SPOT":
        kind = "master_internal" if t_master else "sub_internal"
        legs.append(_PlannedLeg(kind, dst_spot, to_ep))

    return legs


# ─────────────────────────────────────────────────────────────────────
# binance type-string mapping for /sapi/v1/asset/transfer
# ─────────────────────────────────────────────────────────────────────

_ASSET_TRANSFER_TYPES: dict[tuple[str, str], str] = {
    ("SPOT", "USDT_FUTURE"): "MAIN_UMFUTURE",
    ("USDT_FUTURE", "SPOT"): "UMFUTURE_MAIN",
    ("SPOT", "COIN_FUTURE"): "MAIN_CMFUTURE",
    ("COIN_FUTURE", "SPOT"): "CMFUTURE_MAIN",
    ("SPOT", "MARGIN"): "MAIN_MARGIN",
    ("MARGIN", "SPOT"): "MARGIN_MAIN",
    ("SPOT", "OPTION"): "MAIN_OPTION",
    ("OPTION", "SPOT"): "OPTION_MAIN",
    ("USDT_FUTURE", "OPTION"): "UMFUTURE_OPTION",
    ("OPTION", "USDT_FUTURE"): "OPTION_UMFUTURE",
    ("MARGIN", "OPTION"): "MARGIN_OPTION",
    ("OPTION", "MARGIN"): "OPTION_MARGIN",
    ("USDT_FUTURE", "MARGIN"): "UMFUTURE_MARGIN",
    ("MARGIN", "USDT_FUTURE"): "MARGIN_UMFUTURE",
}


def _asset_transfer_type(from_wt: str, to_wt: str) -> str:
    key = (from_wt, to_wt)
    if key not in _ASSET_TRANSFER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported asset-transfer pair: {from_wt} → {to_wt}. "
                "If you need this, route through SPOT."
            ),
        )
    return _ASSET_TRANSFER_TYPES[key]


# ─────────────────────────────────────────────────────────────────────
# leg execution
# ─────────────────────────────────────────────────────────────────────


def _generate_client_tran_id(
    *,
    user_id: str,
    intent_id: str,
    leg_index: int,
    leg_total: int,
) -> str:
    """Deterministic per-intent, per-leg idempotency key (≤32 alphanumeric)."""
    safe_user = "".join(ch for ch in user_id if ch.isalnum())[:6] or "user"
    safe_intent = "".join(ch for ch in intent_id if ch.isalnum())[:18]
    return f"{safe_user}{safe_intent}{leg_index:01d}{leg_total:01d}"[:32]


def _leg_reason(leg_index: int, leg_total: int) -> str:
    if leg_total == 1:
        return "manual"
    return f"manual:leg{leg_index}/{leg_total}"


async def _master_internal_transfer(
    *,
    master_client: BinanceSubAccountClient,
    leg: _PlannedLeg,
    asset: str,
    amount: Decimal,
    client_tran_id: str,
) -> dict[str, Any]:
    transfer_type = _asset_transfer_type(
        leg.from_ep.wallet_type, leg.to_ep.wallet_type
    )
    # asset/transfer does not accept clientTranId for every type pairing, so
    # we pass the binance-supported subset only when the docs guarantee it
    # (omitting is always safe because we still write a row keyed by our
    # own client_tran_id for our audit log).
    try:
        return await master_client.master_asset_transfer(
            transfer_type=transfer_type,
            asset=asset,
            amount=amount,
            client_tran_id=client_tran_id,
        )
    except TypeError:
        # Defensive: older client signature without client_tran_id
        return await master_client.master_asset_transfer(
            transfer_type=transfer_type,
            asset=asset,
            amount=amount,
        )


async def _sub_internal_transfer(  # noqa: PLR0913 — every kwarg is required
    *,
    session: AsyncSession,
    client_factory: BinanceClientFactory,
    leg: _PlannedLeg,
    asset: str,
    amount: Decimal,
    client_tran_id: str,
) -> dict[str, Any]:
    if leg.from_ep.account is None:
        raise HTTPException(
            status_code=500, detail="sub_internal leg missing sub account"
        )
    sub_account = leg.from_ep.account
    if not sub_account.api_key_enc or not sub_account.api_secret_enc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"sub-account '{sub_account.alias}' has no API key configured "
                "— add one under Settings → Sub account → (row) to enable "
                "Options transfers."
            ),
        )
    transfer_type = _asset_transfer_type(
        leg.from_ep.wallet_type, leg.to_ep.wallet_type
    )
    crypto = get_crypto_service()
    api_key = crypto.decrypt(sub_account.api_key_enc)
    api_secret = crypto.decrypt(sub_account.api_secret_enc)
    # Build a short-lived client bound to the sub's own credentials. We
    # intentionally do not cache: this code path is manual, infrequent,
    # and bypassing the factory keeps the cache invariants simple.
    client = BinanceSubAccountClient(api_key=api_key, api_secret=api_secret)
    try:
        return await client.master_asset_transfer(
            transfer_type=transfer_type,
            asset=asset,
            amount=amount,
            client_tran_id=client_tran_id,
        )
    finally:
        await client.aclose()


async def _sub_universal_transfer(
    *,
    master_client: BinanceSubAccountClient,
    leg: _PlannedLeg,
    asset: str,
    amount: Decimal,
    client_tran_id: str,
) -> dict[str, Any]:
    from_email = None if leg.from_ep.is_master else leg.from_ep.email
    to_email = None if leg.to_ep.is_master else leg.to_ep.email
    return await master_client.universal_transfer(
        from_account_type=leg.from_ep.wallet_type,
        to_account_type=leg.to_ep.wallet_type,
        asset=asset,
        amount=amount,
        from_email=from_email,
        to_email=to_email,
        client_tran_id=client_tran_id,
    )


def _earn_endpoint(leg: _PlannedLeg) -> _Endpoint:
    """The ``_Endpoint`` whose key owns the Simple Earn position.

    For an ``earn_subscribe`` leg the destination is the EARN side; for an
    ``earn_redeem`` leg the source is the EARN side.
    """
    if leg.from_ep.wallet_type == "EARN_FLEXIBLE":
        return leg.from_ep
    return leg.to_ep


async def _resolve_earn_keys(
    session: AsyncSession,
    *,
    user_id: str,
    env: str,
    earn_ep: _Endpoint,
) -> tuple[str, str]:
    """Return ``(api_key, api_secret)`` for the wallet that holds the Earn position.

    Master uses the master credential; sub uses the sub's own credential.
    Raises a 400 ``HTTPException`` with an actionable message when keys
    are missing.
    """
    crypto = get_crypto_service()
    if earn_ep.is_master:
        master = await get_master_wallet_account(
            session, user_id=user_id, env=env
        )
        if master is None or not master.api_key_enc or not master.api_secret_enc:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"master wallet for env={env} has no API key configured "
                    "— Simple Earn transfers require an authenticated master "
                    "credential."
                ),
            )
        return crypto.decrypt(master.api_key_enc), crypto.decrypt(
            master.api_secret_enc
        )

    sub_account = earn_ep.account
    if (
        sub_account is None
        or not sub_account.api_key_enc
        or not sub_account.api_secret_enc
    ):
        alias = sub_account.alias if sub_account else "?"
        raise HTTPException(
            status_code=400,
            detail=(
                f"sub-account '{alias}' has no API key configured — Simple "
                "Earn transfers require the sub's own key. Add one under "
                "Settings → Sub account → (row)."
            ),
        )
    return (
        crypto.decrypt(sub_account.api_key_enc),
        crypto.decrypt(sub_account.api_secret_enc),
    )


async def _earn_transfer(  # noqa: PLR0913 — every kwarg is required
    *,
    session: AsyncSession,
    user_id: str,
    env: str,
    leg: _PlannedLeg,
    asset: str,
    amount: Decimal,
) -> dict[str, Any]:
    """Subscribe to or redeem from Simple Earn Flexible.

    ``leg.kind`` is one of ``earn_subscribe`` / ``earn_redeem`` /
    ``sub_earn_subscribe`` / ``sub_earn_redeem``. The direction determines
    which Binance endpoint we call; the *_sub variants merely use the sub's
    own key instead of the master's.
    """
    if asset not in EARN_SUPPORTED_ASSETS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Simple Earn transfers currently only support: "
                f"{', '.join(sorted(EARN_SUPPORTED_ASSETS))} "
                f"(received {asset})."
            ),
        )

    earn_ep = _earn_endpoint(leg)
    api_key, api_secret = await _resolve_earn_keys(
        session, user_id=user_id, env=env, earn_ep=earn_ep
    )
    if env != "mainnet":
        raise HTTPException(
            status_code=400,
            detail="Simple Earn transfers are only available on mainnet.",
        )

    client = BinanceEarnClient(api_key=api_key, api_secret=api_secret)
    try:
        product_id = await client.get_usdt_flexible_product_id()
        if not product_id:
            raise HTTPException(
                status_code=502,
                detail="Could not resolve USDT Flexible product on Binance.",
            )
        if leg.kind in ("earn_subscribe", "sub_earn_subscribe"):
            return await client.subscribe(
                amount=float(amount), product_id=product_id
            )
        # redeem
        return await client.redeem(
            amount=float(amount), product_id=product_id, redeem_all=False
        )
    finally:
        await client.aclose()


def _extract_tran_id(response: dict[str, Any] | None) -> str | None:
    if not response:
        return None
    for key in ("tranId", "txnId", "id"):
        value = response.get(key)
        if value is not None:
            return str(value)
    return None


async def _execute_leg(  # noqa: PLR0913 — every kwarg is required
    *,
    session: AsyncSession,
    client_factory: BinanceClientFactory,
    master_client: BinanceSubAccountClient,
    leg: _PlannedLeg,
    leg_index: int,
    leg_total: int,
    user_id: str,
    env: str,
    asset: str,
    amount: Decimal,
    intent_id: str,
) -> WalletTransfer:
    """Run one leg with full audit-log + idempotency semantics."""
    client_tran_id = _generate_client_tran_id(
        user_id=user_id,
        intent_id=intent_id,
        leg_index=leg_index,
        leg_total=leg_total,
    )
    reason = _leg_reason(leg_index, leg_total)

    existing = await get_wallet_transfer_by_client_id(
        session, client_tran_id=client_tran_id
    )
    if existing is not None:
        status_value = (
            existing.status.value
            if hasattr(existing.status, "value")
            else existing.status
        )
        if status_value == WalletTransferStatus.SUCCEEDED.value:
            return existing
        if status_value == WalletTransferStatus.FAILED.value:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"previous leg {client_tran_id} FAILED — submit again "
                    "to retry with a fresh intent id"
                ),
            )
        transfer = existing
    else:
        transfer = await create_wallet_transfer(
            session,
            user_id=user_id,
            from_wallet_account_id=leg.from_ep.account_id,
            to_wallet_account_id=leg.to_ep.account_id,
            from_wallet_type=leg.from_ep.wallet_type,
            to_wallet_type=leg.to_ep.wallet_type,
            asset=asset,
            amount=float(amount),
            reason=reason,
            client_tran_id=client_tran_id,
            status=WalletTransferStatus.PENDING,
        )
        await session.commit()

    try:
        if leg.kind == "master_internal":
            response = await _master_internal_transfer(
                master_client=master_client,
                leg=leg,
                asset=asset,
                amount=amount,
                client_tran_id=client_tran_id,
            )
        elif leg.kind == "sub_internal":
            response = await _sub_internal_transfer(
                session=session,
                client_factory=client_factory,
                leg=leg,
                asset=asset,
                amount=amount,
                client_tran_id=client_tran_id,
            )
        elif leg.kind == "sub_universal":
            response = await _sub_universal_transfer(
                master_client=master_client,
                leg=leg,
                asset=asset,
                amount=amount,
                client_tran_id=client_tran_id,
            )
        elif leg.kind in (
            "earn_subscribe",
            "earn_redeem",
            "sub_earn_subscribe",
            "sub_earn_redeem",
        ):
            response = await _earn_transfer(
                session=session,
                user_id=user_id,
                env=env,
                leg=leg,
                asset=asset,
                amount=amount,
            )
        else:  # pragma: no cover — exhaustive
            raise HTTPException(
                status_code=500, detail=f"unknown leg kind: {leg.kind}"
            )
    except (
        BinanceSubAccountClientError,
        BinanceOptionsClientError,
        BinanceEarnClientError,
    ) as exc:
        msg = str(exc)
        try:
            await mark_wallet_transfer_failed(
                session, transfer_id=transfer.id, error_message=msg
            )
            await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark transfer %s FAILED", transfer.id)
        msg_short = msg[:160] + ("..." if len(msg) > 160 else "")
        raise HTTPException(
            status_code=502,
            detail=(
                f"leg {leg_index}/{leg_total} "
                f"({leg.from_ep.label}→{leg.to_ep.label}) failed: {msg_short}"
            ),
        ) from exc

    binance_tran_id = _extract_tran_id(response)
    await mark_wallet_transfer_succeeded(
        session, transfer_id=transfer.id, binance_tran_id=binance_tran_id
    )
    await session.commit()
    await session.refresh(transfer)
    return transfer


# ─────────────────────────────────────────────────────────────────────
# endpoint resolution
# ─────────────────────────────────────────────────────────────────────


async def _resolve_endpoint(  # noqa: PLR0913 — kwargs are distinct dimensions
    session: AsyncSession,
    *,
    user_id: str,
    env: str,
    wallet_account_id: str | None,
    wallet_type: str,
    label: str,
) -> _Endpoint:
    """Validate and load an endpoint description into an :class:`_Endpoint`."""
    if wallet_type not in UI_WALLET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: invalid wallet_type '{wallet_type}'",
        )

    if wallet_account_id is None:
        # Master endpoint — still verify a master wallet exists so we have
        # an env to bind to.
        master = await get_master_wallet_account(
            session, user_id=user_id, env=env
        )
        if master is None:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: no master wallet configured for env={env}",
            )
        return _Endpoint(account=None, wallet_type=wallet_type)

    try:
        wid = uuid.UUID(wallet_account_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: invalid wallet_account_id",
        ) from exc

    wallet = await get_wallet_account(session, wallet_account_id=wid)
    if wallet is None or wallet.user_id != user_id:
        raise HTTPException(
            status_code=404, detail=f"{label}: wallet not found"
        )
    if wallet.env != env:
        raise HTTPException(
            status_code=400,
            detail=f"{label}: wallet env mismatch (expected {env}, got {wallet.env})",
        )
    # Soft permission check: the wallet's ``enabled_wallets`` flags only
    # apply to sub-accounts (master has all wallets). ``OPTION`` is allowed
    # if explicitly enabled or if the entry is missing (we trust Binance to
    # reject it at the transfer call).
    if wallet.enabled_wallets and wallet_type != "SPOT":
        enabled_map = {
            "USDT_FUTURE": "futures_um",
            "COIN_FUTURE": "futures_cm",
            "MARGIN": "margin",
            "OPTION": "options",
        }
        flag = enabled_map.get(wallet_type)
        if flag and wallet.enabled_wallets.get(flag) is False:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{label}: wallet '{wallet.alias}' does not have {wallet_type} "
                    f"enabled. Enable it on Binance and run Sync."
                ),
            )
    return _Endpoint(account=wallet, wallet_type=wallet_type)


# ─────────────────────────────────────────────────────────────────────
# balance aggregation
# ─────────────────────────────────────────────────────────────────────


def _cells_from_spot(rows: list[dict[str, Any]]) -> list[WalletBalanceCell]:
    out: list[WalletBalanceCell] = []
    for row in rows:
        asset = str(row.get("asset") or "")
        if asset not in SUPPORTED_ASSETS:
            continue
        free = float(row.get("free", 0) or 0)
        locked = float(row.get("locked", 0) or 0)
        total = free + locked
        if total > 0:
            out.append(
                WalletBalanceCell(asset=asset, free=free, locked=locked, total=total)
            )
    return out


def _cells_from_margin(snapshot: dict[str, Any]) -> list[WalletBalanceCell]:
    rows = snapshot.get("userAssets") or []
    out: list[WalletBalanceCell] = []
    for row in rows:
        asset = str(row.get("asset") or "")
        if asset not in SUPPORTED_ASSETS:
            continue
        free = float(row.get("free", 0) or 0)
        locked = float(row.get("locked", 0) or 0)
        total = free + locked
        if total > 0:
            out.append(
                WalletBalanceCell(asset=asset, free=free, locked=locked, total=total)
            )
    return out


def _cells_from_futures(snapshot: dict[str, Any]) -> list[WalletBalanceCell]:
    # ``/sapi/v2/sub-account/futures/account`` returns ``{assets: [...]}``
    # while master's ``/fapi/v2/account`` returns the same shape.
    rows = snapshot.get("assets") or []
    out: list[WalletBalanceCell] = []
    for row in rows:
        asset = str(row.get("asset") or "")
        if asset not in SUPPORTED_ASSETS:
            continue
        free = float(row.get("availableBalance", row.get("free", 0)) or 0)
        total = float(row.get("walletBalance", free) or free)
        locked = max(0.0, total - free)
        if total > 0:
            out.append(
                WalletBalanceCell(asset=asset, free=free, locked=locked, total=total)
            )
    return out


def _cells_from_options(snapshot: dict[str, Any]) -> list[WalletBalanceCell]:
    rows = snapshot.get("asset") or []
    out: list[WalletBalanceCell] = []
    for row in rows:
        asset = str(row.get("asset") or "")
        if asset not in SUPPORTED_ASSETS:
            continue
        free = float(row.get("available", 0) or 0)
        equity = float(row.get("equity", 0) or 0)
        locked = max(0.0, equity - free)
        if equity > 0:
            out.append(
                WalletBalanceCell(
                    asset=asset, free=free, locked=locked, total=equity
                )
            )
    return out


async def _fetch_master_balances(  # noqa: PLR0915 — multiple sibling fetchers, one per wallet type
    master_client: BinanceSubAccountClient,
    *,
    env: str,
    earn_client: BinanceEarnClient | None = None,
    master_api_key: str | None = None,
    master_api_secret: str | None = None,
) -> tuple[dict[str, list[WalletBalanceCell]], dict[str, str]]:
    """Return ``(balances, errors)`` for the master account.

    Each wallet type is fetched concurrently and any individual failure
    is captured in ``errors`` so the UI can still render the rest. When
    ``earn_client`` is provided we additionally fetch the USDT Flexible
    position; otherwise the EARN_FLEXIBLE column is left blank. When
    ``master_api_key``/``master_api_secret`` are provided we also fetch
    USDⓈ-M Futures, COIN-M Futures, and Options snapshots — each on its
    own (one-shot) httpx client because Binance hosts these on distinct
    base URLs (``fapi``/``dapi``/``eapi``).
    """
    balances: dict[str, list[WalletBalanceCell]] = {}
    errors: dict[str, str] = {}

    async def _spot() -> None:
        try:
            data = await master_client.get_user_asset(asset=None)
            balances["SPOT"] = _cells_from_spot(data)
        except Exception as exc:  # noqa: BLE001
            errors["SPOT"] = str(exc)

    async def _margin() -> None:
        try:
            data = await master_client.get_margin_account()
            balances["MARGIN"] = _cells_from_margin(data)
        except Exception as exc:  # noqa: BLE001
            errors["MARGIN"] = str(exc)

    async def _earn() -> None:
        if earn_client is None:
            return
        try:
            usdt = await earn_client.fetch_flexible_position_usdt()
            if usdt > 0:
                balances["EARN_FLEXIBLE"] = [
                    WalletBalanceCell(
                        asset="USDT", free=usdt, locked=0.0, total=usdt
                    )
                ]
            else:
                balances["EARN_FLEXIBLE"] = []
        except Exception as exc:  # noqa: BLE001
            errors["EARN_FLEXIBLE"] = str(exc)

    async def _futures_um() -> None:
        if not (master_api_key and master_api_secret):
            return
        try:
            data = await fetch_um_futures_account(
                api_key=master_api_key, api_secret=master_api_secret
            )
            balances["USDT_FUTURE"] = _cells_from_futures(data)
        except (BinanceFuturesBalanceError, Exception) as exc:  # noqa: BLE001
            errors["USDT_FUTURE"] = str(exc)

    async def _futures_cm() -> None:
        if not (master_api_key and master_api_secret):
            return
        try:
            data = await fetch_cm_futures_account(
                api_key=master_api_key, api_secret=master_api_secret
            )
            balances["COIN_FUTURE"] = _cells_from_futures(data)
        except (BinanceFuturesBalanceError, Exception) as exc:  # noqa: BLE001
            errors["COIN_FUTURE"] = str(exc)

    async def _options() -> None:
        if not (master_api_key and master_api_secret):
            return
        client = BinanceOptionsClient(
            api_key=master_api_key,
            api_secret=master_api_secret,
            base_url=resolve_options_base_url(env),
        )
        try:
            data = await client.fetch_account()
            balances["OPTION"] = _cells_from_options(data)
        except (BinanceOptionsClientError, Exception) as exc:  # noqa: BLE001
            errors["OPTION"] = str(exc)
        finally:
            await client.aclose()

    await asyncio.gather(
        _spot(), _margin(), _earn(), _futures_um(), _futures_cm(), _options()
    )
    return balances, errors


async def _fetch_sub_balances(  # noqa: PLR0915 — multiple sibling fetchers, one per wallet type
    master_client: BinanceSubAccountClient,
    *,
    wallet: WalletAccount,
) -> tuple[dict[str, list[WalletBalanceCell]], dict[str, str]]:
    balances: dict[str, list[WalletBalanceCell]] = {}
    errors: dict[str, str] = {}
    email = wallet.sub_account_email or ""

    async def _spot_margin() -> None:
        try:
            data = await master_client.get_subaccount_assets(email)
            balances["SPOT"] = _cells_from_spot(data.get("balances") or [])
        except Exception as exc:  # noqa: BLE001
            errors["SPOT"] = str(exc)

    async def _futures_um() -> None:
        if wallet.enabled_wallets and wallet.enabled_wallets.get("futures_um") is False:
            return
        try:
            data = await master_client.get_sub_futures_account(
                email, futures_type=1
            )
            balances["USDT_FUTURE"] = _cells_from_futures(data)
        except Exception as exc:  # noqa: BLE001
            errors["USDT_FUTURE"] = str(exc)

    async def _futures_cm() -> None:
        if wallet.enabled_wallets and wallet.enabled_wallets.get("futures_cm") is False:
            return
        try:
            data = await master_client.get_sub_futures_account(
                email, futures_type=2
            )
            balances["COIN_FUTURE"] = _cells_from_futures(data)
        except Exception as exc:  # noqa: BLE001
            errors["COIN_FUTURE"] = str(exc)

    async def _options() -> None:
        # Sub-account Options requires the sub's own API key (master key
        # cannot read /eapi/v1/account for a sub).
        if wallet.enabled_wallets and wallet.enabled_wallets.get("options") is False:
            return
        if not wallet.api_key_enc or not wallet.api_secret_enc:
            return
        crypto = get_crypto_service()
        try:
            api_key = crypto.decrypt(wallet.api_key_enc)
            api_secret = crypto.decrypt(wallet.api_secret_enc)
        except Exception as exc:  # noqa: BLE001
            errors["OPTION"] = f"decrypt: {exc}"
            return
        env_str = (wallet.env.value if hasattr(wallet.env, "value") else wallet.env) or "mainnet"
        client = BinanceOptionsClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=resolve_options_base_url(str(env_str)),
        )
        try:
            data = await client.fetch_account()
            balances["OPTION"] = _cells_from_options(data)
        except (BinanceOptionsClientError, Exception) as exc:  # noqa: BLE001
            errors["OPTION"] = str(exc)
        finally:
            await client.aclose()

    async def _earn() -> None:
        # Sub-account Earn positions are only readable with the sub's own
        # key. Skip silently when the key is missing so the row stays clean;
        # otherwise reuse the EARN client to fetch the USDT Flexible balance.
        if not wallet.api_key_enc or not wallet.api_secret_enc:
            return
        crypto = get_crypto_service()
        try:
            api_key = crypto.decrypt(wallet.api_key_enc)
            api_secret = crypto.decrypt(wallet.api_secret_enc)
        except Exception as exc:  # noqa: BLE001
            errors["EARN_FLEXIBLE"] = f"decrypt: {exc}"
            return
        client = BinanceEarnClient(api_key=api_key, api_secret=api_secret)
        try:
            usdt = await client.fetch_flexible_position_usdt()
            balances["EARN_FLEXIBLE"] = (
                [WalletBalanceCell(asset="USDT", free=usdt, locked=0.0, total=usdt)]
                if usdt > 0
                else []
            )
        except Exception as exc:  # noqa: BLE001
            errors["EARN_FLEXIBLE"] = str(exc)
        finally:
            await client.aclose()

    await asyncio.gather(
        _spot_margin(), _futures_um(), _futures_cm(), _options(), _earn()
    )
    return balances, errors


# ─────────────────────────────────────────────────────────────────────
# routes
# ─────────────────────────────────────────────────────────────────────


def register_transfer_routes(  # noqa: PLR0915 — single function defines all wallet-transfer routes
    app: FastAPI,
    *,
    require_auth_dep: AuthDep,
    db_session_dep: SessionDep,
) -> None:
    """Attach ``/api/me/wallet-balances`` and ``/api/me/wallet-transfers``."""

    _auth_param = Depends(require_auth_dep)
    _session_param = Depends(db_session_dep)

    @app.get("/api/me/wallet-balances", response_model=WalletBalancesOut)
    async def get_balances(
        env: str = "mainnet",
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> WalletBalancesOut:
        if env not in {"mainnet", "testnet"}:
            raise HTTPException(status_code=400, detail="invalid env")
        factory = get_client_factory()
        try:
            master_client = await factory.get_master_subaccount_client(
                session, user_id=user.user_id, env=env
            )
        except BinanceClientFactoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        wallets = await list_wallet_accounts(
            session, user_id=user.user_id, env=env
        )
        # Master row first
        master = next(
            (
                w
                for w in wallets
                if (
                    w.role.value
                    if hasattr(w.role, "value")
                    else w.role
                )
                == "master"
            ),
            None,
        )
        rows: list[WalletBalanceRow] = []
        if master is not None:
            # Best-effort Earn client built from the master credential.
            earn_client: BinanceEarnClient | None = None
            master_api_key: str | None = None
            master_api_secret: str | None = None
            if (
                env == "mainnet"
                and master.api_key_enc
                and master.api_secret_enc
            ):
                try:
                    crypto = get_crypto_service()
                    master_api_key = crypto.decrypt(master.api_key_enc)
                    master_api_secret = crypto.decrypt(master.api_secret_enc)
                    earn_client = BinanceEarnClient(
                        api_key=master_api_key,
                        api_secret=master_api_secret,
                    )
                except Exception:  # noqa: BLE001
                    earn_client = None
                    master_api_key = None
                    master_api_secret = None
            try:
                mb, me = await _fetch_master_balances(
                    master_client,
                    env=env,
                    earn_client=earn_client,
                    master_api_key=master_api_key,
                    master_api_secret=master_api_secret,
                )
            finally:
                if earn_client is not None:
                    await earn_client.aclose()
            rows.append(
                WalletBalanceRow(
                    wallet_account_id=None,
                    role="master",
                    alias=master.alias or "master",
                    env=env,
                    email=master.sub_account_email,
                    enabled_wallets={
                        "spot": True,
                        "futures_um": True,
                        "futures_cm": True,
                        "margin": True,
                        "options": True,
                        "earn": True,
                    },
                    balances=mb,
                    errors=me,
                )
            )
        subs = [
            w
            for w in wallets
            if (w.role.value if hasattr(w.role, "value") else w.role) == "sub"
        ]
        for sub in subs:
            sb, se = await _fetch_sub_balances(master_client, wallet=sub)
            rows.append(
                WalletBalanceRow(
                    wallet_account_id=str(sub.id),
                    role="sub",
                    alias=sub.alias or "sub",
                    env=env,
                    email=sub.sub_account_email,
                    enabled_wallets=dict(sub.enabled_wallets or {}),
                    balances=sb,
                    errors=se,
                )
            )
        return WalletBalancesOut(
            ts=datetime.now(UTC).isoformat(),
            rows=rows,
        )

    @app.post("/api/me/wallet-transfers", response_model=TransferOut)
    async def execute_transfer(
        body: TransferIn,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> TransferOut:
        if body.asset not in SUPPORTED_ASSETS:
            raise HTTPException(
                status_code=400,
                detail=f"asset '{body.asset}' not supported (v1: {', '.join(SUPPORTED_ASSETS)})",
            )
        amount = Decimal(str(body.amount))
        if amount <= 0:
            raise HTTPException(status_code=400, detail="amount must be > 0")

        from_ep = await _resolve_endpoint(
            session,
            user_id=user.user_id,
            env=body.env,
            wallet_account_id=body.from_wallet_account_id,
            wallet_type=body.from_wallet_type,
            label="from",
        )
        to_ep = await _resolve_endpoint(
            session,
            user_id=user.user_id,
            env=body.env,
            wallet_account_id=body.to_wallet_account_id,
            wallet_type=body.to_wallet_type,
            label="to",
        )

        plan = _build_plan(from_ep, to_ep)
        intent_id = uuid.uuid4().hex[:18]
        factory = get_client_factory()
        try:
            master_client = await factory.get_master_subaccount_client(
                session, user_id=user.user_id, env=body.env
            )
        except BinanceClientFactoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        leg_total = len(plan)
        legs_out: list[TransferLegOut] = []
        for idx, leg in enumerate(plan, start=1):
            transfer = await _execute_leg(
                session=session,
                client_factory=factory,
                master_client=master_client,
                leg=leg,
                leg_index=idx,
                leg_total=leg_total,
                user_id=user.user_id,
                env=body.env,
                asset=body.asset,
                amount=amount,
                intent_id=intent_id,
            )
            legs_out.append(_transfer_to_leg_out(transfer, idx, leg_total))

        return TransferOut(
            ok=True,
            intent_id=intent_id,
            leg_total=leg_total,
            legs=legs_out,
        )

    @app.get(
        "/api/me/wallet-transfers", response_model=list[TransferLegOut]
    )
    async def list_transfers(
        limit: int = 50,
        user: Any = _auth_param,
        session: AsyncSession = _session_param,
    ) -> list[TransferLegOut]:
        limit = max(1, min(int(limit), 500))
        rows = await list_wallet_transfers(
            session, user_id=user.user_id, limit=limit
        )
        # ``list_wallet_transfers`` already returns rows in created_at desc.
        # ``leg_index``/``leg_total`` are derived from the ``reason`` field
        # so the UI can group multi-leg transfers visually.
        out: list[TransferLegOut] = []
        for row in rows:
            idx, total = _parse_leg_marker(row.reason)
            out.append(_transfer_to_leg_out(row, idx, total))
        return out


# ─────────────────────────────────────────────────────────────────────
# serialization helpers
# ─────────────────────────────────────────────────────────────────────


def _parse_leg_marker(reason: str | None) -> tuple[int, int]:
    """Extract ``(leg_index, leg_total)`` from ``"manual:legN/M"`` strings."""
    if not reason or not reason.startswith("manual:leg"):
        return (1, 1)
    try:
        body = reason[len("manual:leg") :]  # noqa: E203
        idx_s, total_s = body.split("/", 1)
        return (int(idx_s), int(total_s))
    except (ValueError, IndexError):
        return (1, 1)


def _transfer_to_leg_out(
    row: WalletTransfer, leg_index: int, leg_total: int
) -> TransferLegOut:
    status = row.status.value if hasattr(row.status, "value") else row.status
    return TransferLegOut(
        id=str(row.id),
        leg_index=leg_index,
        leg_total=leg_total,
        from_wallet_account_id=(
            str(row.from_wallet_account_id)
            if row.from_wallet_account_id
            else None
        ),
        to_wallet_account_id=(
            str(row.to_wallet_account_id)
            if row.to_wallet_account_id
            else None
        ),
        from_wallet_type=row.from_wallet_type,
        to_wallet_type=row.to_wallet_type,
        asset=row.asset,
        amount=float(row.amount),
        status=status,
        binance_tran_id=row.binance_tran_id,
        client_tran_id=row.client_tran_id,
        error_message=row.error_message,
        created_at=row.created_at.isoformat() if row.created_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


__all__ = [
    "SUPPORTED_ASSETS",
    "UI_WALLET_TYPES",
    "register_transfer_routes",
]


# Silence unused-import warnings — the imports above are part of the
# documented public surface (used in tests / future-proofing).
_ = (BinanceOptionsClient, resolve_options_base_url, VALID_WALLET_TYPES)
