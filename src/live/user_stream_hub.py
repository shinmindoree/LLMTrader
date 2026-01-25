"""User Stream Hub.

단일 Binance User Stream을 여러 컨텍스트(심볼별 LiveContext 등)가 공유할 수 있도록
이벤트를 fan-out 한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from binance.client import BinanceHTTPClient
from binance.user_stream import BinanceUserStream


class UserStreamHub:
    """단일 User Stream을 여러 핸들러로 fan-out."""

    def __init__(self, client: BinanceHTTPClient) -> None:
        self.client = client
        self._handlers: list[Callable[[dict[str, Any]], Awaitable[None]]] = []
        self._on_disconnect_handlers: list[Callable[[], Awaitable[None]]] = []
        self._on_reconnect_handlers: list[Callable[[bool], Awaitable[None]]] = []
        self._stream: BinanceUserStream | None = None
        self._task: asyncio.Task[None] | None = None

    def register_handler(self, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._handlers.append(handler)

    def register_disconnect_handler(self, handler: Callable[[], Awaitable[None]]) -> None:
        self._on_disconnect_handlers.append(handler)

    def register_reconnect_handler(self, handler: Callable[[bool], Awaitable[None]]) -> None:
        self._on_reconnect_handlers.append(handler)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return

        is_testnet = "testnet" in self.client.base_url.lower()
        self._stream = BinanceUserStream(
            client=self.client,
            callback=self._dispatch,
            testnet=is_testnet,
            on_disconnect=self._on_disconnect,
            on_reconnect=self._on_reconnect,
        )
        self._task = asyncio.create_task(self._stream.start())

    async def stop(self) -> None:
        if self._stream:
            await self._stream.stop()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                pass
        self._task = None
        self._stream = None

    async def _dispatch(self, data: dict[str, Any]) -> None:
        for handler in list(self._handlers):
            try:
                await handler(data)
            except Exception:  # noqa: BLE001
                # 각 컨텍스트의 내부 예외로 인해 전체 user stream이 죽지 않도록 보호
                continue

    async def _on_disconnect(self) -> None:
        for handler in list(self._on_disconnect_handlers):
            try:
                await handler()
            except Exception:  # noqa: BLE001
                continue

    async def _on_reconnect(self, is_actual_disconnect: bool) -> None:
        for handler in list(self._on_reconnect_handlers):
            try:
                await handler(is_actual_disconnect)
            except Exception:  # noqa: BLE001
                continue

