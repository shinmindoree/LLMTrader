from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control.enums import EventKind
from control.repo import append_event, insert_trade, upsert_order


def _sanitize_for_json(obj: Any) -> Any:
    """Decimal 등 JSON 직렬화 불가 타입을 float/str로 변환한다."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


@dataclass(frozen=True)
class EventItem:
    job_id: uuid.UUID
    kind: EventKind
    message: str
    level: str
    payload: dict[str, Any] | None


class DbEventSink:
    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        job_id: uuid.UUID,
        max_queue: int = 10_000,
    ) -> None:
        self._session_maker = session_maker
        self._job_id = job_id
        self._queue: asyncio.Queue[EventItem] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._loop = asyncio.get_running_loop()

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name=f"db-event-sink:{self._job_id}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def emit(
        self,
        *,
        kind: EventKind,
        message: str,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> None:
        item = EventItem(job_id=self._job_id, kind=kind, message=message, level=level, payload=payload)
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            # drop oldest on overflow by draining a bit
            try:
                _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(item)

    def emit_from_thread(
        self,
        *,
        kind: EventKind,
        message: str,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._loop.call_soon_threadsafe(
            partial(self.emit, kind=kind, message=message, level=level, payload=payload)
        )

    def audit_hook(self, action: str, data: dict[str, Any]) -> None:
        kind = EventKind.LOG
        if action.startswith("ORDER_") or action.startswith("CHASE_"):
            kind = EventKind.ORDER
        elif action.startswith("STOPLOSS_") or action.startswith("PNL_") or action.startswith("RISK_"):
            kind = EventKind.RISK
        self.emit(kind=kind, message=action, payload=data)

        if kind == EventKind.ORDER:
            symbol = str(data.get("symbol") or "")
            inner = data.get("data")
            if isinstance(inner, dict) and symbol:
                asyncio.create_task(
                    self.record_order_from_audit(action, inner, symbol=symbol),
                    name=f"record-order:{self._job_id}",
                )

        inner = data.get("data")
        symbol = str(data.get("symbol") or "")
        if isinstance(inner, dict) and symbol:
            trade = inner.get("trade")
            if isinstance(trade, dict):
                reason = inner.get("reason")
                exit_reason = inner.get("exit_reason")
                asyncio.create_task(
                    self.record_trade_from_user_trade(
                        trade, symbol=symbol, reason=reason, exit_reason=exit_reason
                    ),
                    name=f"record-trade:{self._job_id}",
                )

    async def record_order_from_audit(self, action: str, data: dict[str, Any], *, symbol: str) -> None:
        order_id_val = data.get("order_id")
        if order_id_val is None:
            return
        try:
            order_id = int(order_id_val)
        except (TypeError, ValueError):
            return

        side = str(data.get("side") or "")
        order_type = str(data.get("type") or data.get("order_type") or "")
        status = action.replace("ORDER_", "")
        qty = data.get("quantity") or data.get("executed_qty")
        price = data.get("price") or data.get("avg_price")
        executed_qty = data.get("executed_qty")
        avg_price = data.get("avg_price")

        async with self._session_maker() as session:
            await upsert_order(
                session,
                job_id=self._job_id,
                symbol=symbol,
                order_id=order_id,
                side=side,
                order_type=order_type,
                status=status,
                quantity=float(qty) if qty is not None else None,
                price=float(price) if price is not None else None,
                executed_qty=float(executed_qty) if executed_qty is not None else None,
                avg_price=float(avg_price) if avg_price is not None else None,
                raw_json=_sanitize_for_json(data),
            )
            await session.commit()

    async def record_trade_from_user_trade(
        self,
        trade: dict[str, Any],
        *,
        symbol: str,
        reason: Any = None,
        exit_reason: Any = None,
    ) -> None:
        try:
            trade_id = int(trade.get("id"))
        except (TypeError, ValueError):
            return
        order_id = trade.get("orderId")
        order_id_int = int(order_id) if order_id is not None else None
        qty = float(trade.get("qty")) if trade.get("qty") is not None else None
        price = float(trade.get("price")) if trade.get("price") is not None else None
        realized_pnl = float(trade.get("realizedPnl")) if trade.get("realizedPnl") is not None else None
        commission = float(trade.get("commission")) if trade.get("commission") is not None else None

        raw_json = dict(trade)
        if reason is not None:
            raw_json["reason"] = reason
        if exit_reason is not None:
            raw_json["exit_reason"] = exit_reason

        async with self._session_maker() as session:
            await insert_trade(
                session,
                job_id=self._job_id,
                symbol=symbol,
                trade_id=trade_id,
                order_id=order_id_int,
                quantity=qty,
                price=price,
                realized_pnl=realized_pnl,
                commission=commission,
                raw_json=_sanitize_for_json(raw_json),
            )
            await session.commit()

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            async with self._session_maker() as session:
                await append_event(
                    session,
                    job_id=item.job_id,
                    kind=item.kind,
                    message=item.message,
                    level=item.level,
                    payload_json=item.payload,
                )
                await session.commit()
