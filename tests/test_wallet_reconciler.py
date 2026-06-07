"""Unit tests for ``live.wallet_reconciler``.

The reconciler is the heart of Option A — Binance is the source of truth
for the sub-account roster, the app is a mirror. We pin its decision
table here so regressions get caught before they reach a live cycle.

All tests run against in-memory fakes so they're fast and hermetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

import live.wallet_reconciler as wr
from binance.subaccount_client import BinanceSubAccountClientError
from control.models import WalletAccountStatus
from live.wallet_reconciler import (
    ReconcileSummary,
    WalletReconciler,
    reconcile_snapshot_key,
)

# ── fakes ────────────────────────────────────────────────────────────


class _FakeWallet:
    """Minimal stand-in for the SQLAlchemy ``WalletAccount`` model."""

    def __init__(  # noqa: PLR0913 — mirrors the SQLAlchemy model surface
        self,
        *,
        wid: str,
        role: str = "sub",
        email: str | None = None,
        status: str = "active",
        api_key_enc: str | None = "enc::xyz",
        alias: str | None = None,
        purpose: str = "generic",
        enabled_wallets: dict[str, Any] | None = None,
    ) -> None:
        self.id = wid
        self.role = role
        self.sub_account_email = email
        self.status = status
        self.api_key_enc = api_key_enc
        self.alias = alias or (
            email.split("@", 1)[0] if email else f"sub-{wid}"
        )
        self.purpose = purpose
        self.enabled_wallets = dict(enabled_wallets or {})


class _FakeSnapshot:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data_json = data


class _FakeSession:
    """Async-context-manager-friendly session that records mutations.

    We don't actually run SQL; we just record what the reconciler asked
    for so the assertions can check intent without any DB at all.
    """

    def __init__(self) -> None:
        self.committed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


def _make_session_maker(session: _FakeSession):
    def factory() -> _FakeSession:
        return session

    return factory


def _patch_repo(monkeypatch: pytest.MonkeyPatch, *, db_subs: list[_FakeWallet]):
    """Stub out the repo / factory functions the reconciler depends on."""
    list_calls: list[dict[str, Any]] = []
    status_updates: list[tuple[str, str]] = []
    meta_updates: list[dict[str, Any]] = []
    snapshots: list[tuple[str, dict[str, Any]]] = []
    created: list[dict[str, Any]] = []

    async def fake_list_wallet_accounts(_session, *, user_id, env):
        list_calls.append({"user_id": user_id, "env": env})
        # mimic ``list_wallet_accounts`` returning all roles; reconciler
        # filters to ``sub`` itself.
        return db_subs

    async def fake_update_status(_session, *, wallet_account_id, status):
        status_value = status.value if hasattr(status, "value") else status
        status_updates.append((wallet_account_id, status_value))
        # also mutate the in-memory wallet so subsequent checks see it
        for w in db_subs:
            if w.id == wallet_account_id:
                w.status = status_value
                break

    async def fake_update_meta(
        _session,
        *,
        wallet_account_id,
        enabled_wallets=None,
        ip_whitelist=None,
        purpose=None,
    ):
        meta_updates.append(
            {
                "wallet_account_id": wallet_account_id,
                "enabled_wallets": enabled_wallets,
                "ip_whitelist": ip_whitelist,
                "purpose": purpose,
            }
        )
        for w in db_subs:
            if w.id == wallet_account_id:
                if enabled_wallets is not None:
                    w.enabled_wallets = enabled_wallets
                break

    async def fake_create_wallet_account(  # noqa: PLR0913 — mirrors repo
        _session,
        *,
        user_id,
        env,
        role,
        alias,
        purpose="generic",
        sub_account_email=None,
        api_key_enc=None,
        api_secret_enc=None,
        enabled_wallets=None,
        ip_whitelist=None,
        status=None,
    ):
        role_value = role.value if hasattr(role, "value") else role
        status_value = status.value if hasattr(status, "value") else status
        new = _FakeWallet(
            wid=f"new-{len(created) + 1}",
            role=role_value,
            email=sub_account_email,
            status=status_value or "key_missing",
            api_key_enc=api_key_enc,
            alias=alias,
            purpose=purpose,
            enabled_wallets=enabled_wallets or {},
        )
        created.append(
            {
                "user_id": user_id,
                "env": env,
                "role": role_value,
                "alias": alias,
                "purpose": purpose,
                "email": sub_account_email,
                "enabled_wallets": enabled_wallets,
                "ip_whitelist": ip_whitelist,
                "status": status_value,
            }
        )
        db_subs.append(new)
        return new

    async def fake_upsert_snapshot(_session, *, key, data_json):
        snapshots.append((key, data_json))

    async def fake_get_snapshot(_session, *, key):
        for k, payload in reversed(snapshots):
            if k == key:
                return _FakeSnapshot(payload)
        return None

    monkeypatch.setattr(wr, "list_wallet_accounts", fake_list_wallet_accounts)
    monkeypatch.setattr(wr, "update_wallet_account_status", fake_update_status)
    monkeypatch.setattr(wr, "update_wallet_account_meta", fake_update_meta)
    monkeypatch.setattr(wr, "create_wallet_account", fake_create_wallet_account)
    monkeypatch.setattr(wr, "upsert_account_snapshot", fake_upsert_snapshot)
    monkeypatch.setattr(wr, "get_account_snapshot", fake_get_snapshot)

    return {
        "list_calls": list_calls,
        "status_updates": status_updates,
        "meta_updates": meta_updates,
        "snapshots": snapshots,
        "created": created,
    }


def _make_reconciler(
    *, session: _FakeSession, master_client: Any, monkeypatch: pytest.MonkeyPatch
) -> WalletReconciler:
    class _FakeFactory:
        get_master_subaccount_client = AsyncMock(return_value=master_client)

    # Make sure permission-sync's get_status() call never blows up the
    # cycle for tests that don't care about permissions.
    if not hasattr(master_client, "get_status"):
        master_client.get_status = AsyncMock(return_value=[])
    factory = _FakeFactory()
    return WalletReconciler(
        session_maker=_make_session_maker(session),  # type: ignore[arg-type]
        client_factory=factory,  # type: ignore[arg-type]
        min_interval_sec=0,
    )


# ── tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_marks_missing_when_sub_disappears_from_binance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB has a sub that Binance no longer lists → mark binance_missing."""
    db_subs = [
        _FakeWallet(wid="w1", email="orphan_001@subs.binance.com", status="active"),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(return_value=[])  # nothing on Binance

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1", env="mainnet")

    assert summary.ok is True
    assert summary.binance_subs == 0
    assert summary.db_subs == 1
    assert summary.marked_missing == ["orphan_001@subs.binance.com"]
    assert summary.marked_disabled == []
    assert tracking["status_updates"] == [
        ("w1", WalletAccountStatus.BINANCE_MISSING.value)
    ]
    assert session.committed is True


@pytest.mark.asyncio
async def test_reconcile_marks_disabled_when_binance_freezes_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binance returns isFreeze=true → mirror as ``disabled`` in DB."""
    db_subs = [
        _FakeWallet(wid="w1", email="dir_001@subs.binance.com", status="active"),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[{"email": "dir_001@subs.binance.com", "isFreeze": True}]
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert summary.marked_disabled == ["dir_001@subs.binance.com"]
    assert tracking["status_updates"] == [
        ("w1", WalletAccountStatus.DISABLED.value)
    ]


@pytest.mark.asyncio
async def test_reconcile_clears_stale_missing_when_sub_reappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sub previously marked ``binance_missing`` shows up again.

    With a key present we recover to ``active``; without a key, to
    ``key_missing`` (so the user re-enters the trading key).
    """
    db_subs = [
        _FakeWallet(
            wid="w1",
            email="dir_001@subs.binance.com",
            status="binance_missing",
            api_key_enc="enc::abc",
        ),
        _FakeWallet(
            wid="w2",
            email="arb_001@subs.binance.com",
            status="binance_missing",
            api_key_enc=None,
        ),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[
            {"email": "dir_001@subs.binance.com", "isFreeze": False},
            {"email": "arb_001@subs.binance.com", "isFreeze": False},
        ]
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert sorted(summary.cleared_missing) == [
        "arb_001@subs.binance.com",
        "dir_001@subs.binance.com",
    ]
    updates = dict(tracking["status_updates"])
    assert updates["w1"] == WalletAccountStatus.ACTIVE.value
    assert updates["w2"] == WalletAccountStatus.KEY_MISSING.value


@pytest.mark.asyncio
async def test_reconcile_leaves_operator_disabled_state_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator manually set ``disabled`` — reconcile must not auto-revive."""
    db_subs = [
        _FakeWallet(wid="w1", email="dir_001@subs.binance.com", status="disabled"),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[{"email": "dir_001@subs.binance.com", "isFreeze": False}]
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert summary.marked_missing == []
    assert summary.marked_disabled == []
    assert summary.cleared_missing == []
    assert tracking["status_updates"] == []  # no changes


@pytest.mark.asyncio
async def test_reconcile_auto_creates_binance_only_subs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binance has subs the DB has never heard of → mirror into DB.

    Aliases derive from the email local-part (Binance UX convention),
    and collisions get a numeric suffix so the unique constraint never
    fires.
    """
    db_subs = [
        _FakeWallet(
            wid="w1",
            email="dir_001@subs.binance.com",
            status="active",
            alias="dir_001",
        ),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[
            {"email": "dir_001@subs.binance.com", "isFreeze": False},
            {"email": "extra_999@subs.binance.com", "isFreeze": False},
            # collision with existing alias "dir_001" — must dedupe
            {"email": "dir_001@othersubs.binance.com", "isFreeze": False},
        ]
    )
    master_client.get_status = AsyncMock(
        return_value=[
            {
                "email": "extra_999@subs.binance.com",
                "isFutureEnabled": True,
                "isMarginEnabled": False,
            }
        ]
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert summary.unmanaged_binance_subs == []  # field retained but unused
    created_emails = sorted(c["email"] for c in tracking["created"])
    assert created_emails == [
        "dir_001@othersubs.binance.com",
        "extra_999@subs.binance.com",
    ]
    # alias collision was resolved with -2 suffix
    aliases = {c["email"]: c["alias"] for c in tracking["created"]}
    assert aliases["extra_999@subs.binance.com"] == "extra_999"
    assert aliases["dir_001@othersubs.binance.com"] == "dir_001-2"
    # auto-created rows arrive in key_missing with derived enabled_wallets
    extra = next(c for c in tracking["created"] if c["email"].startswith("extra_999"))
    assert extra["status"] == WalletAccountStatus.KEY_MISSING.value
    assert extra["enabled_wallets"]["futures_um"] is True
    assert extra["enabled_wallets"]["margin"] is False
    assert sorted(summary.auto_created) == created_emails


@pytest.mark.asyncio
async def test_reconcile_marks_auto_created_disabled_when_binance_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Newly-discovered subs that come in frozen should land as ``disabled``."""
    db_subs: list[_FakeWallet] = []
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[
            {"email": "frozen_001@subs.binance.com", "isFreeze": True},
        ]
    )
    master_client.get_status = AsyncMock(return_value=[])

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert tracking["created"][0]["status"] == WalletAccountStatus.DISABLED.value
    assert summary.auto_created == ["frozen_001@subs.binance.com"]


@pytest.mark.asyncio
async def test_reconcile_syncs_permissions_from_binance_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """isFutureEnabled / isMarginEnabled flow into enabled_wallets."""
    db_subs = [
        _FakeWallet(
            wid="w1",
            email="dir_001@subs.binance.com",
            status="active",
            enabled_wallets={"spot": True, "futures_um": False, "margin": False},
        ),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[{"email": "dir_001@subs.binance.com", "isFreeze": False}]
    )
    master_client.get_status = AsyncMock(
        return_value=[
            {
                "email": "dir_001@subs.binance.com",
                "isFutureEnabled": True,
                "isMarginEnabled": True,
            }
        ]
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert summary.permissions_synced == ["dir_001@subs.binance.com"]
    assert tracking["meta_updates"][-1]["enabled_wallets"] == {
        "spot": True,
        "futures_um": True,
        "margin": True,
    }


@pytest.mark.asyncio
async def test_reconcile_skips_permission_write_when_already_in_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotent — same permissions in DB should not generate a write."""
    db_subs = [
        _FakeWallet(
            wid="w1",
            email="dir_001@subs.binance.com",
            status="active",
            enabled_wallets={"spot": True, "futures_um": True, "margin": False},
        ),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[{"email": "dir_001@subs.binance.com", "isFreeze": False}]
    )
    master_client.get_status = AsyncMock(
        return_value=[
            {
                "email": "dir_001@subs.binance.com",
                "isFutureEnabled": True,
                "isMarginEnabled": False,
            }
        ]
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert summary.permissions_synced == []
    assert tracking["meta_updates"] == []


@pytest.mark.asyncio
async def test_reconcile_is_noop_when_everything_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All active subs present and not frozen → no writes, summary clean."""
    db_subs = [
        _FakeWallet(wid="w1", email="dir_001@subs.binance.com", status="active"),
        _FakeWallet(wid="w2", email="arb_001@subs.binance.com", status="active"),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        return_value=[
            {"email": "dir_001@subs.binance.com", "isFreeze": False},
            {"email": "arb_001@subs.binance.com", "isFreeze": False},
        ]
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u1")

    assert summary.ok is True
    assert summary.marked_missing == []
    assert summary.marked_disabled == []
    assert summary.cleared_missing == []
    assert summary.unmanaged_binance_subs == []
    assert tracking["status_updates"] == []


@pytest.mark.asyncio
async def test_reconcile_persists_snapshot_for_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_last_summary`` returns the most recently persisted payload."""
    db_subs: list[_FakeWallet] = []
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(return_value=[])

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u-snap")
    # exactly one snapshot row written, keyed correctly
    snaps = tracking["snapshots"]
    assert len(snaps) == 1
    key, payload = snaps[0]
    assert key == reconcile_snapshot_key("u-snap")
    assert payload["user_id"] == "u-snap"
    assert payload["env"] == "mainnet"
    # round-trip via get_last_summary
    fetched = await rec.get_last_summary(user_id="u-snap")
    assert fetched is not None
    assert fetched["ts"] == summary.ts


@pytest.mark.asyncio
async def test_reconcile_soft_fails_on_binance_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binance failures land in ``summary.error``; nothing is raised."""
    db_subs = [
        _FakeWallet(wid="w1", email="dir_001@subs.binance.com", status="active"),
    ]
    tracking = _patch_repo(monkeypatch, db_subs=db_subs)

    master_client = AsyncMock()
    master_client.list_subaccounts = AsyncMock(
        side_effect=BinanceSubAccountClientError("rate limited")
    )

    session = _FakeSession()
    rec = _make_reconciler(
        session=session, master_client=master_client, monkeypatch=monkeypatch
    )

    summary = await rec.reconcile_user(user_id="u-err")

    assert summary.ok is False
    assert summary.error and "rate limited" in summary.error
    # error path still persists the failure summary
    assert any(k == reconcile_snapshot_key("u-err") for k, _ in tracking["snapshots"])
    # but does not mutate wallet status
    assert tracking["status_updates"] == []


def test_reconcile_summary_payload_round_trips() -> None:
    """``to_payload`` must produce a dict that the FastAPI schema accepts."""
    s = ReconcileSummary(
        user_id="u",
        env="mainnet",
        ok=True,
        ts=datetime.now(UTC).isoformat(),
        binance_subs=3,
        db_subs=3,
    )
    payload = s.to_payload()
    assert set(payload) >= {
        "user_id",
        "env",
        "ok",
        "ts",
        "binance_subs",
        "db_subs",
        "marked_missing",
        "marked_disabled",
        "cleared_missing",
        "auto_created",
        "permissions_synced",
        "unmanaged_binance_subs",
        "error",
    }
