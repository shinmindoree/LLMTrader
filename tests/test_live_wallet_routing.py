from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from runner.executors import live_executor


@dataclass
class _Wallet:
    user_id: str
    env: str


class _SessionContext:
    def __init__(self) -> None:
        self.session = object()

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _SessionMaker:
    def __init__(self) -> None:
        self.context = _SessionContext()

    def __call__(self) -> _SessionContext:
        return self.context


class _Factory:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []
        self.client = object()

    async def get_trading_client(self, session: object, *, wallet_account_id: str) -> object:
        self.calls.append((session, wallet_account_id))
        return self.client


@pytest.mark.asyncio
async def test_resolve_binance_client_uses_selected_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wallet_id = uuid.uuid4()
    session_maker = _SessionMaker()
    factory = _Factory()

    async def fake_get_wallet_account(_session: Any, *, wallet_account_id: uuid.UUID) -> _Wallet:
        assert wallet_account_id == wallet_id
        return _Wallet(user_id="u1", env="mainnet")

    monkeypatch.setattr(live_executor, "get_wallet_account", fake_get_wallet_account)
    monkeypatch.setattr(live_executor, "get_client_factory", lambda: factory)

    client, earn_client = await live_executor._resolve_binance_client(
        "u1",
        session_maker,
        "mainnet",
        wallet_account_id=wallet_id,
    )

    assert client is factory.client
    assert earn_client is None
    assert factory.calls == [(session_maker.context.session, str(wallet_id))]


@pytest.mark.asyncio
async def test_resolve_binance_client_rejects_wallet_env_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wallet_id = uuid.uuid4()
    factory = _Factory()

    async def fake_get_wallet_account(_session: Any, *, wallet_account_id: uuid.UUID) -> _Wallet:
        assert wallet_account_id == wallet_id
        return _Wallet(user_id="u1", env="testnet")

    monkeypatch.setattr(live_executor, "get_wallet_account", fake_get_wallet_account)
    monkeypatch.setattr(live_executor, "get_client_factory", lambda: factory)

    with pytest.raises(ValueError, match="does not match job env"):
        await live_executor._resolve_binance_client(
            "u1",
            _SessionMaker(),
            "mainnet",
            wallet_account_id=wallet_id,
        )

    assert factory.calls == []
