"""FLIP close→reverse-entry simulation.

Drives a real :class:`LiveContext` against a fake Binance client to verify
that a FLIP (close the current position and immediately enter the opposite
side) actually opens the reverse leg — including the case where the first
maker (GTX/post-only) placement is rejected with ``-5022`` and has to be
retried (the exact churn seen in the live event log).

These tests deliberately use ``_use_user_stream = False`` so position
bookkeeping is driven by the executed-qty calculation + REST account
snapshot, exactly like the live runner falls back to.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import pytest

from common.risk import RiskConfig
from live.context import LiveContext
from live.risk import LiveRiskManager

SYMBOL = "BTCUSDT"
PRICE = 62000.0


class FakeClient:
    """Minimal Binance-futures stand-in.

    Tracks an authoritative ``position`` and records every fill as a user
    trade. ``reject_codes`` is a queue of bools: when the next entry is
    ``True`` the upcoming ``place_order`` raises a ``-5022`` post-only
    rejection (then the bool is consumed), simulating a maker order that
    could not rest on the book.
    """

    def __init__(self, *, price: float, reject_queue: list[bool] | None = None) -> None:
        self.position = 0.0
        self.entry_price = 0.0
        self.price = price
        self._seq = 1000
        self.trades: list[dict[str, Any]] = []
        self.reject_queue = list(reject_queue or [])
        self.placed: list[dict[str, Any]] = []

    # ── order placement ────────────────────────────────────
    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: str,
        type: str,  # noqa: A002 - mirror real client kwarg name
        price: str | None = None,
        timeInForce: str | None = None,
        reduceOnly: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        if self.reject_queue and self.reject_queue.pop(0):
            raise RuntimeError(
                "Binance API error: 400 POST /fapi/v1/order | "
                "payload={'code': -5022, 'msg': 'Due to the order could not be "
                "executed as maker, the Post Only order will be rejected.'}"
            )
        oid = self._seq
        self._seq += 1
        qty = float(quantity)
        signed = qty if side == "BUY" else -qty
        fill_price = float(price) if price is not None else self.price

        prev = self.position
        # Realized PnL only on the reducing portion of the trade.
        realized = 0.0
        if prev != 0.0 and (prev > 0) != (side == "BUY"):
            reduce_qty = min(qty, abs(prev))
            direction = 1.0 if prev > 0 else -1.0
            realized = (fill_price - self.entry_price) * reduce_qty * direction

        new_pos = prev + signed
        if abs(new_pos) > 1e-12 and (abs(prev) < 1e-12 or (prev > 0) == (signed > 0)):
            self.entry_price = fill_price  # opening / adding
        self.position = new_pos

        commission = abs(qty) * fill_price * 0.0002
        self.placed.append({"side": side, "qty": qty, "reduceOnly": reduceOnly})
        self.trades.append(
            {
                "id": oid * 10,
                "orderId": oid,
                "symbol": symbol,
                "side": side,
                "qty": str(qty),
                "price": str(fill_price),
                "quoteQty": str(qty * fill_price),
                "commission": str(commission),
                "commissionAsset": "USDT",
                "realizedPnl": str(realized),
                "maker": True,
                "buyer": side == "BUY",
                "time": int(time.time() * 1000),
                "positionSide": "BOTH",
            }
        )
        return {
            "orderId": oid,
            "status": "FILLED",
            "executedQty": str(qty),
            "avgPrice": str(fill_price),
            "price": str(fill_price),
            "side": side,
            "type": type,
        }

    async def fetch_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        return {"orderId": order_id, "status": "FILLED", "executedQty": "0"}

    async def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        return {"orderId": order_id, "status": "CANCELED"}

    async def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return []

    async def fetch_user_trades(
        self, symbol: str, start_time: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return list(self.trades)

    async def _signed_request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if path == "/fapi/v2/account":
            return {
                "multiAssetsMargin": False,
                "assets": [{"asset": "USDT", "walletBalance": "5000"}],
                "availableBalance": "5000",
                "positions": [
                    {
                        "symbol": SYMBOL,
                        "positionAmt": str(self.position),
                        "entryPrice": str(self.entry_price),
                        "unrealizedProfit": "0",
                    }
                ],
            }
        return {}


def _make_ctx(client: FakeClient) -> LiveContext:
    risk = LiveRiskManager(
        RiskConfig(
            max_position_size=1.0,
            daily_loss_limit=100000.0,
            max_consecutive_losses=0,
            max_order_size=0.5,
        )
    )
    ctx = LiveContext(client=client, risk_manager=risk, symbol=SYMBOL, leverage=1, env="test")
    ctx._use_user_stream = False
    ctx.balance = 5000.0
    ctx.available_balance = 5000.0
    ctx._current_price = PRICE
    ctx.step_size = Decimal("0.001")
    ctx.tick_size = Decimal("0.1")
    ctx.min_qty = Decimal("0.001")
    ctx.max_qty = Decimal("1000")
    ctx.min_notional = Decimal("5")
    ctx._best_bid = Decimal(str(PRICE - 1))
    ctx._best_ask = Decimal(str(PRICE + 1))
    # tighten chase loop so the simulation is fast
    ctx._chase_interval = 0.0
    ctx._chase_max_attempts = 3
    return ctx


async def _pump(seconds: float = 1.0) -> None:
    """Let the chained order/after-fill/entry tasks finish."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        await asyncio.sleep(0.01)
        pending = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task()
        ]
        if not pending:
            return


def _actions(ctx: LiveContext) -> list[str]:
    return [e["action"] for e in ctx.audit_log]


@pytest.mark.asyncio
async def test_flip_long_to_short_clean() -> None:
    """A clean maker FLIP opens the reverse short leg."""
    client = FakeClient(price=PRICE)
    ctx = _make_ctx(client)

    # Start long.
    ctx.position.size = 0.016
    ctx.position.entry_price = PRICE
    client.position = 0.016
    client.entry_price = PRICE

    ctx.flip_position(
        target_side=-1,
        close_reason="MFP: net direction flip (long->short)",
        entry_reason="MFP: net short",
    )
    await _pump()

    actions = _actions(ctx)
    assert "FLIP_SCHEDULED" in actions
    assert "FLIP_REJECTED" not in actions
    # Final position must be the reverse (short) leg, not flat.
    assert ctx.position.size < -1e-9, f"reverse leg missing: size={ctx.position.size}"
    # Exchange truth agrees.
    assert client.position < -1e-9
    # Exactly one ORDER_FILLED EXIT (close) and one ENTRY (reverse).
    events = [
        e["data"].get("event")
        for e in ctx.audit_log
        if e["action"] == "ORDER_FILLED"
    ]
    assert "EXIT" in events
    assert "ENTRY" in events


@pytest.mark.asyncio
async def test_flip_short_to_long_clean() -> None:
    """The mirror case: short → long."""
    client = FakeClient(price=PRICE)
    ctx = _make_ctx(client)

    ctx.position.size = -0.016
    ctx.position.entry_price = PRICE
    client.position = -0.016
    client.entry_price = PRICE

    ctx.flip_position(
        target_side=1,
        close_reason="MFP: net direction flip (short->long)",
        entry_reason="MFP: net long",
    )
    await _pump()

    assert "FLIP_SCHEDULED" in _actions(ctx)
    assert ctx.position.size > 1e-9, f"reverse leg missing: size={ctx.position.size}"
    assert client.position > 1e-9


@pytest.mark.asyncio
async def test_flip_survives_post_only_5022_on_close() -> None:
    """The first maker placement is rejected with -5022 (post-only), exactly
    like the live log. The close must still complete and the reverse leg must
    still open."""
    # Reject only the very first place_order call (the close's first GTX try).
    client = FakeClient(price=PRICE, reject_queue=[True])
    ctx = _make_ctx(client)

    ctx.position.size = 0.031
    ctx.position.entry_price = PRICE
    client.position = 0.031
    client.entry_price = PRICE

    ctx.flip_position(
        target_side=-1,
        close_reason="MFP: net direction flip (long->short)",
        entry_reason="MFP: net short",
    )
    await _pump()

    actions = _actions(ctx)
    assert "CHASE_ORDER_ERROR" in actions  # the -5022 was hit
    assert "FLIP_SCHEDULED" in actions
    assert "FLIP_REJECTED" not in actions
    # Despite the rejection churn, the reverse short leg is open.
    assert ctx.position.size < -1e-9, f"reverse leg missing: size={ctx.position.size}"
    assert client.position < -1e-9
