"""Unit tests for ``api.transfers`` — leg planner and execute path.

The planner is pure logic so most tests are deterministic. The leg
executor is exercised against in-memory fakes so we don't need a live
Binance session or a database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import api.transfers as t


# ── tiny fakes ──────────────────────────────────────────────────────


class _FakeWallet:
    """Minimal SQLAlchemy ``WalletAccount`` stand-in."""

    def __init__(
        self,
        *,
        wid: str,
        alias: str = "sub-a",
        email: str = "subA@example.com",
        api_key_enc: str | None = "enc::key",
        api_secret_enc: str | None = "enc::secret",
        enabled_wallets: dict[str, Any] | None = None,
    ) -> None:
        # Hash the friendly id into a real UUID for downstream comparisons.
        self.id = uuid.uuid5(uuid.NAMESPACE_OID, wid)
        self.alias = alias
        self.sub_account_email = email
        self.api_key_enc = api_key_enc
        self.api_secret_enc = api_secret_enc
        self.enabled_wallets = dict(enabled_wallets or {})


def _ep(account: _FakeWallet | None, wallet_type: str) -> t._Endpoint:
    return t._Endpoint(account=account, wallet_type=wallet_type)


# ── planner ─────────────────────────────────────────────────────────


def test_plan_same_master_internal_non_options() -> None:
    plan = t._build_plan(_ep(None, "SPOT"), _ep(None, "USDT_FUTURE"))
    assert [leg.kind for leg in plan] == ["master_internal"]


def test_plan_same_master_options() -> None:
    plan = t._build_plan(_ep(None, "SPOT"), _ep(None, "OPTION"))
    assert [leg.kind for leg in plan] == ["master_internal"]


def test_plan_same_sub_options_uses_sub_internal() -> None:
    sub = _FakeWallet(wid="sub-a")
    plan = t._build_plan(_ep(sub, "SPOT"), _ep(sub, "OPTION"))
    assert [leg.kind for leg in plan] == ["sub_internal"]


def test_plan_master_to_sub_non_options_is_single_leg() -> None:
    sub = _FakeWallet(wid="sub-a")
    plan = t._build_plan(_ep(None, "SPOT"), _ep(sub, "USDT_FUTURE"))
    assert [leg.kind for leg in plan] == ["sub_universal"]


def test_plan_sub_to_sub_non_options_is_single_leg() -> None:
    a = _FakeWallet(wid="sub-a")
    b = _FakeWallet(wid="sub-b", email="subB@example.com")
    plan = t._build_plan(_ep(a, "USDT_FUTURE"), _ep(b, "USDT_FUTURE"))
    assert [leg.kind for leg in plan] == ["sub_universal"]


def test_plan_master_option_to_sub_option_is_three_legs() -> None:
    sub = _FakeWallet(wid="sub-a")
    plan = t._build_plan(_ep(None, "OPTION"), _ep(sub, "OPTION"))
    kinds = [leg.kind for leg in plan]
    assert kinds == ["master_internal", "sub_universal", "sub_internal"]
    # The middle leg must funnel through SPOT
    middle = plan[1]
    assert middle.from_ep.wallet_type == "SPOT"
    assert middle.to_ep.wallet_type == "SPOT"


def test_plan_sub_option_to_master_spot_is_two_legs() -> None:
    sub = _FakeWallet(wid="sub-a")
    plan = t._build_plan(_ep(sub, "OPTION"), _ep(None, "SPOT"))
    assert [leg.kind for leg in plan] == ["sub_internal", "sub_universal"]


def test_plan_master_spot_to_sub_option_is_two_legs() -> None:
    sub = _FakeWallet(wid="sub-a")
    plan = t._build_plan(_ep(None, "SPOT"), _ep(sub, "OPTION"))
    assert [leg.kind for leg in plan] == ["sub_universal", "sub_internal"]


def test_plan_identical_endpoint_is_rejected() -> None:
    sub = _FakeWallet(wid="sub-a")
    with pytest.raises(HTTPException) as ei:
        t._build_plan(_ep(sub, "SPOT"), _ep(sub, "SPOT"))
    assert ei.value.status_code == 400


# ── asset/transfer type mapping ─────────────────────────────────────


def test_asset_transfer_type_known_pairs() -> None:
    assert t._asset_transfer_type("SPOT", "OPTION") == "MAIN_OPTION"
    assert t._asset_transfer_type("OPTION", "SPOT") == "OPTION_MAIN"
    assert t._asset_transfer_type("SPOT", "USDT_FUTURE") == "MAIN_UMFUTURE"


def test_asset_transfer_type_rejects_unknown_pair() -> None:
    with pytest.raises(HTTPException) as ei:
        t._asset_transfer_type("COIN_FUTURE", "OPTION")
    assert ei.value.status_code == 400


# ── id generation ──────────────────────────────────────────────────


def test_generate_client_tran_id_is_short_and_alphanumeric() -> None:
    tid = t._generate_client_tran_id(
        user_id="user-123",
        intent_id="abcd1234efgh5678",
        leg_index=2,
        leg_total=3,
    )
    assert tid.isalnum()
    assert len(tid) <= 32


def test_generate_client_tran_id_is_deterministic() -> None:
    a = t._generate_client_tran_id(
        user_id="u", intent_id="iid", leg_index=1, leg_total=1
    )
    b = t._generate_client_tran_id(
        user_id="u", intent_id="iid", leg_index=1, leg_total=1
    )
    assert a == b


def test_leg_reason_marker() -> None:
    assert t._leg_reason(1, 1) == "manual"
    assert t._leg_reason(2, 3) == "manual:leg2/3"


def test_parse_leg_marker_round_trip() -> None:
    assert t._parse_leg_marker("manual") == (1, 1)
    assert t._parse_leg_marker("manual:leg2/3") == (2, 3)
    assert t._parse_leg_marker(None) == (1, 1)
    assert t._parse_leg_marker("garbage") == (1, 1)


# ── balance cell extraction ─────────────────────────────────────────


def test_cells_from_spot_filters_zero_and_unsupported() -> None:
    rows = [
        {"asset": "USDT", "free": "10.5", "locked": "1.5"},
        {"asset": "USDC", "free": "0", "locked": "0"},
        {"asset": "DOGE", "free": "100", "locked": "0"},
    ]
    cells = t._cells_from_spot(rows)
    assert len(cells) == 1
    assert cells[0].asset == "USDT"
    assert cells[0].total == pytest.approx(12.0)


def test_cells_from_futures_uses_wallet_balance() -> None:
    snap = {
        "assets": [
            {"asset": "USDT", "availableBalance": "100", "walletBalance": "150"}
        ]
    }
    cells = t._cells_from_futures(snap)
    assert cells[0].free == pytest.approx(100.0)
    assert cells[0].total == pytest.approx(150.0)
    assert cells[0].locked == pytest.approx(50.0)


def test_cells_from_options_uses_equity() -> None:
    snap = {"asset": [{"asset": "USDT", "available": "80", "equity": "120"}]}
    cells = t._cells_from_options(snap)
    assert cells[0].free == pytest.approx(80.0)
    assert cells[0].total == pytest.approx(120.0)


# ── leg executor — single sub_universal call path ───────────────────


class _FakeSession:
    def __init__(self) -> None:
        self.committed = 0
        self.refreshed: list[Any] = []

    async def commit(self) -> None:
        self.committed += 1

    async def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)


class _FakeTransferRow:
    def __init__(self, **kw: Any) -> None:
        self.id = uuid.uuid4()
        self.user_id = kw.get("user_id")
        self.from_wallet_account_id = kw.get("from_wallet_account_id")
        self.to_wallet_account_id = kw.get("to_wallet_account_id")
        self.from_wallet_type = kw.get("from_wallet_type")
        self.to_wallet_type = kw.get("to_wallet_type")
        self.asset = kw.get("asset")
        self.amount = kw.get("amount")
        self.reason = kw.get("reason")
        self.status = kw.get("status", "PENDING")
        self.client_tran_id = kw.get("client_tran_id")
        self.binance_tran_id = None
        self.error_message = None
        self.created_at = datetime.now(UTC)
        self.completed_at: datetime | None = None


@pytest.fixture
def patch_repo(monkeypatch: pytest.MonkeyPatch):
    """Stub the repo functions ``_execute_leg`` calls."""
    created: list[_FakeTransferRow] = []
    marks: list[dict[str, Any]] = []

    async def fake_get_by_client_id(_session, *, client_tran_id):  # noqa: ARG001
        return None

    async def fake_create(  # noqa: PLR0913 — mirrors repo signature
        _session, *, user_id, from_wallet_account_id, to_wallet_account_id,
        from_wallet_type, to_wallet_type, asset, amount, reason, client_tran_id,
        status,
    ):
        row = _FakeTransferRow(
            user_id=user_id,
            from_wallet_account_id=from_wallet_account_id,
            to_wallet_account_id=to_wallet_account_id,
            from_wallet_type=from_wallet_type,
            to_wallet_type=to_wallet_type,
            asset=asset,
            amount=amount,
            reason=reason,
            client_tran_id=client_tran_id,
            status=getattr(status, "value", status),
        )
        created.append(row)
        return row

    async def fake_mark_succeeded(_session, *, transfer_id, binance_tran_id):
        marks.append({"id": transfer_id, "kind": "succeeded", "tid": binance_tran_id})
        for row in created:
            if row.id == transfer_id:
                row.status = "SUCCEEDED"
                row.binance_tran_id = binance_tran_id

    async def fake_mark_failed(_session, *, transfer_id, error_message):
        marks.append({"id": transfer_id, "kind": "failed", "err": error_message})
        for row in created:
            if row.id == transfer_id:
                row.status = "FAILED"
                row.error_message = error_message

    monkeypatch.setattr(t, "get_wallet_transfer_by_client_id", fake_get_by_client_id)
    monkeypatch.setattr(t, "create_wallet_transfer", fake_create)
    monkeypatch.setattr(t, "mark_wallet_transfer_succeeded", fake_mark_succeeded)
    monkeypatch.setattr(t, "mark_wallet_transfer_failed", fake_mark_failed)
    return {"created": created, "marks": marks}


@pytest.mark.asyncio
async def test_execute_leg_sub_universal_records_success(patch_repo, monkeypatch):
    session = _FakeSession()
    sub = _FakeWallet(wid="sub-a")
    leg = t._PlannedLeg("sub_universal", _ep(None, "SPOT"), _ep(sub, "USDT_FUTURE"))
    master_client = AsyncMock()
    master_client.universal_transfer = AsyncMock(return_value={"tranId": 999})

    row = await t._execute_leg(
        session=session,
        client_factory=None,  # unused for sub_universal
        master_client=master_client,
        leg=leg,
        leg_index=1,
        leg_total=1,
        user_id="u-1",
        env="mainnet",
        asset="USDT",
        amount=Decimal("10"),
        intent_id="iid",
    )
    assert row.status == "SUCCEEDED"
    assert row.binance_tran_id == "999"
    master_client.universal_transfer.assert_awaited_once()
    kw = master_client.universal_transfer.await_args.kwargs
    assert kw["from_account_type"] == "SPOT"
    assert kw["to_account_type"] == "USDT_FUTURE"
    assert kw["to_email"] == "subA@example.com"
    assert kw["from_email"] is None  # master


@pytest.mark.asyncio
async def test_execute_leg_master_internal_uses_asset_transfer(patch_repo):
    session = _FakeSession()
    leg = t._PlannedLeg("master_internal", _ep(None, "SPOT"), _ep(None, "OPTION"))
    master_client = AsyncMock()
    master_client.master_asset_transfer = AsyncMock(return_value={"tranId": 42})

    row = await t._execute_leg(
        session=session,
        client_factory=None,
        master_client=master_client,
        leg=leg,
        leg_index=2,
        leg_total=3,
        user_id="u-1",
        env="mainnet",
        asset="USDT",
        amount=Decimal("5"),
        intent_id="iid",
    )
    assert row.status == "SUCCEEDED"
    master_client.master_asset_transfer.assert_awaited_once()
    kw = master_client.master_asset_transfer.await_args.kwargs
    assert kw["transfer_type"] == "MAIN_OPTION"
    assert kw["asset"] == "USDT"
    # Multi-leg reason marker recorded
    assert row.reason == "manual:leg2/3"


@pytest.mark.asyncio
async def test_execute_leg_sub_internal_requires_sub_key(patch_repo):
    session = _FakeSession()
    sub = _FakeWallet(wid="sub-a", api_key_enc=None, api_secret_enc=None)
    leg = t._PlannedLeg("sub_internal", _ep(sub, "SPOT"), _ep(sub, "OPTION"))
    master_client = AsyncMock()

    with pytest.raises(HTTPException) as ei:
        await t._execute_leg(
            session=session,
            client_factory=None,
            master_client=master_client,
            leg=leg,
            leg_index=1,
            leg_total=1,
            user_id="u-1",
            env="mainnet",
            asset="USDT",
            amount=Decimal("1"),
            intent_id="iid",
        )
    assert ei.value.status_code == 400
    assert "API key" in ei.value.detail


@pytest.mark.asyncio
async def test_execute_leg_marks_failed_on_binance_error(patch_repo):
    from binance.subaccount_client import BinanceSubAccountClientError

    session = _FakeSession()
    sub = _FakeWallet(wid="sub-a")
    leg = t._PlannedLeg("sub_universal", _ep(None, "SPOT"), _ep(sub, "SPOT"))
    master_client = AsyncMock()
    master_client.universal_transfer = AsyncMock(
        side_effect=BinanceSubAccountClientError("insufficient balance")
    )

    with pytest.raises(HTTPException) as ei:
        await t._execute_leg(
            session=session,
            client_factory=None,
            master_client=master_client,
            leg=leg,
            leg_index=1,
            leg_total=1,
            user_id="u-1",
            env="mainnet",
            asset="USDT",
            amount=Decimal("1"),
            intent_id="iid",
        )
    assert ei.value.status_code == 502
    assert any(m["kind"] == "failed" for m in patch_repo["marks"])


@pytest.mark.asyncio
async def test_execute_leg_returns_existing_succeeded(patch_repo, monkeypatch):
    """Idempotency: re-running with the same client_tran_id is a no-op."""
    session = _FakeSession()
    sub = _FakeWallet(wid="sub-a")
    leg = t._PlannedLeg("sub_universal", _ep(None, "SPOT"), _ep(sub, "SPOT"))
    cached = _FakeTransferRow(
        user_id="u-1",
        from_wallet_type="SPOT",
        to_wallet_type="SPOT",
        asset="USDT",
        amount=1,
        reason="manual",
        client_tran_id="cached",
        status="SUCCEEDED",
    )
    cached.binance_tran_id = "abc"

    async def fake_get(_session, *, client_tran_id):  # noqa: ARG001
        return cached

    monkeypatch.setattr(t, "get_wallet_transfer_by_client_id", fake_get)
    master_client = AsyncMock()
    master_client.universal_transfer = AsyncMock()

    row = await t._execute_leg(
        session=session,
        client_factory=None,
        master_client=master_client,
        leg=leg,
        leg_index=1,
        leg_total=1,
        user_id="u-1",
        env="mainnet",
        asset="USDT",
        amount=Decimal("1"),
        intent_id="iid",
    )
    assert row is cached
    master_client.universal_transfer.assert_not_awaited()
