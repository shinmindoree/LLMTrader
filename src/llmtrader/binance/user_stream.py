"""바이낸스 퓨처스 유저데이터 웹소켓 스트림."""

import asyncio
import json
from typing import Any, Awaitable, Callable

import aiohttp

from llmtrader.binance.client import BinanceHTTPClient


class BinanceUserStream:
    """바이낸스 퓨처스 유저데이터 스트림 클라이언트."""

    def __init__(
        self,
        client: BinanceHTTPClient,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        testnet: bool = False,
        keepalive_interval: float = 25 * 60.0,
    ) -> None:
        """유저데이터 스트림 초기화.

        Args:
            client: 바이낸스 HTTP 클라이언트
            callback: 웹소켓 메시지 수신 시 호출될 콜백 함수
            testnet: 테스트넷 사용 여부
            keepalive_interval: listenKey 갱신 주기(초)
        """
        self.client = client
        self.callback = callback
        self.testnet = testnet
        self.keepalive_interval = keepalive_interval
        self.base_url = (
            "wss://stream.binancefuture.com/ws"
            if testnet
            else "wss://fstream.binance.com/ws"
        )
        self.running = False
        self._listen_key: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._keepalive_task: asyncio.Task | None = None

    async def start(self) -> None:
        """유저데이터 스트림 시작 (자동 재연결 포함)."""
        self.running = True

        while self.running:
            reconnect = False
            try:
                self._listen_key = await self.client.create_listen_key()
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())

                url = f"{self.base_url}/{self._listen_key}"
                self._session = aiohttp.ClientSession()
                async with self._session.ws_connect(url) as ws:
                    self._ws = ws
                    print("User Stream connected")

                    async for msg in ws:
                        if not self.running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except Exception:  # noqa: BLE001
                                continue

                            if data.get("e") == "listenKeyExpired":
                                print("User Stream listenKey expired")
                                reconnect = True
                                break

                            await self.callback(data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            reconnect = True
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSE:
                            reconnect = True
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                if self.running:
                    print(f"User Stream reconnecting after error: {exc}")
                    reconnect = True
                else:
                    break
            finally:
                await self._stop_keepalive()
                if self._session:
                    await self._session.close()
                    self._session = None
                self._ws = None

            if self.running and reconnect:
                await asyncio.sleep(5)

        await self._close_listen_key()

    async def stop(self) -> None:
        """유저데이터 스트림 중지."""
        self.running = False
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None
        await self._stop_keepalive()
        await self._close_listen_key()

    async def _keepalive_loop(self) -> None:
        while self.running and self._listen_key:
            await asyncio.sleep(self.keepalive_interval)
            if not self.running or not self._listen_key:
                break
            try:
                await self.client.keepalive_listen_key(self._listen_key)
            except Exception as exc:  # noqa: BLE001
                print(f"User Stream keepalive failed: {exc}")

    async def _stop_keepalive(self) -> None:
        if not self._keepalive_task:
            return
        self._keepalive_task.cancel()
        try:
            await self._keepalive_task
        except asyncio.CancelledError:
            pass
        self._keepalive_task = None

    async def _close_listen_key(self) -> None:
        if not self._listen_key:
            return
        try:
            await self.client.close_listen_key(self._listen_key)
        except Exception:  # noqa: BLE001
            pass
        self._listen_key = None
