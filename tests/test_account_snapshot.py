from __future__ import annotations

from typing import Any

import pytest

from runner.account_snapshot import fetch_snapshot_from_client


class _FakeClient:
    base_url = "https://fapi.binance.com"

    def __init__(self) -> None:
        self.closed = False

    async def fetch_account_info(self) -> dict[str, Any]:
        return {
            "totalWalletBalance": "511.34",
            "totalUnrealizedProfit": "1.25",
            "totalMarginBalance": "512.59",
            "availableBalance": "500.00",
            "canTrade": True,
            "assets": [
                {
                    "asset": "USDT",
                    "walletBalance": "511.34",
                    "availableBalance": "500.00",
                    "unrealizedProfit": "1.25",
                    "marginBalance": "512.59",
                }
            ],
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.01",
                    "entryPrice": "100000",
                    "breakEvenPrice": "100010",
                    "unrealizedProfit": "1.25",
                    "notional": "1000",
                    "leverage": "1",
                    "isolated": False,
                    "updateTime": "0",
                }
            ],
        }

    async def fetch_ticker_price(self, symbol: str) -> float:
        assert symbol == "BTCUSDT"
        return 100000.0

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_fetch_snapshot_from_client_keeps_cached_client_open() -> None:
    client = _FakeClient()

    snapshot = await fetch_snapshot_from_client(client)

    assert snapshot["connected"] is True
    assert snapshot["mode"] == "mainnet"
    assert snapshot["total_wallet_balance"] == 511.34
    assert snapshot["available_balance"] == 500.0
    assert snapshot["positions"][0]["symbol"] == "BTCUSDT"
    assert client.closed is False
