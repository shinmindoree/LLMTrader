"""바이낸스 퓨처스 웹소켓 마켓 스트림."""

import asyncio
import json
from typing import Any, Awaitable, Callable

import aiohttp


class BinanceMarketStream:
    """바이낸스 퓨처스 Kline Stream 웹소켓 클라이언트."""

    def __init__(
        self,
        symbol: str,
        interval: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        testnet: bool = False,
    ) -> None:
        """마켓 스트림 초기화.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)
            interval: 캔들 간격 (예: 1m, 5m, 15m)
            callback: 웹소켓 메시지 수신 시 호출될 콜백 함수
            testnet: 테스트넷 사용 여부
        """
        self.symbol = symbol.lower()
        self.interval = interval
        self.callback = callback
        self.testnet = testnet
        self.base_url = (
            "wss://stream.binancefuture.com/ws"
            if testnet
            else "wss://fstream.binance.com/ws"
        )
        self.stream_name = f"{self.symbol}@kline_{self.interval}"
        self.running = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """웹소켓 스트림 시작 (자동 재연결 포함)."""
        self.running = True
        url = f"{self.base_url}/{self.stream_name}"

        while self.running:
            try:
                self._session = aiohttp.ClientSession()
                async with self._session.ws_connect(url) as ws:
                    self._ws = ws
                    print(f"⚡ Market Stream 연결됨: {self.stream_name} ({'테스트넷' if self.testnet else '라이브'})")

                    async for msg in ws:
                        if not self.running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                await self.callback(data)
                            except Exception as e:  # noqa: BLE001
                                print(f"⚠️ Market Stream 메시지 처리 오류: {e}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"⚠️ Market Stream 오류: {msg.data}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSE:
                            print("⚠️ Market Stream 연결 종료됨")
                            break

            except asyncio.CancelledError:
                print("⚠️ Market Stream 취소됨")
                break
            except Exception as e:  # noqa: BLE001
                if self.running:
                    print(f"⚠️ Market Stream 재연결 대기 중: {e}")
                    await asyncio.sleep(5)
                else:
                    break
            finally:
                if self._session:
                    await self._session.close()
                    self._session = None
                self._ws = None

    async def stop(self) -> None:
        """웹소켓 스트림 중지."""
        self.running = False
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None


class BinanceBookTickerStream:
    """바이낸스 퓨처스 Best Bid/Ask 실시간 스트림."""

    def __init__(
        self,
        symbol: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        testnet: bool = False,
    ) -> None:
        """BookTicker 스트림 초기화.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)
            callback: 웹소켓 메시지 수신 시 호출될 콜백 함수
                      수신 데이터: {"b": "best_bid", "a": "best_ask", ...}
            testnet: 테스트넷 사용 여부
        """
        self.symbol = symbol.lower()
        self.callback = callback
        self.testnet = testnet
        self.base_url = (
            "wss://stream.binancefuture.com/ws"
            if testnet
            else "wss://fstream.binance.com/ws"
        )
        self.stream_name = f"{self.symbol}@bookTicker"
        self.running = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """웹소켓 스트림 시작 (자동 재연결 포함)."""
        self.running = True
        url = f"{self.base_url}/{self.stream_name}"

        while self.running:
            try:
                self._session = aiohttp.ClientSession()
                async with self._session.ws_connect(url) as ws:
                    self._ws = ws
                    print(f"⚡ BookTicker Stream 연결됨: {self.stream_name} ({'테스트넷' if self.testnet else '라이브'})")

                    async for msg in ws:
                        if not self.running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                await self.callback(data)
                            except Exception as e:  # noqa: BLE001
                                print(f"⚠️ BookTicker Stream 메시지 처리 오류: {e}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"⚠️ BookTicker Stream 오류: {msg.data}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSE:
                            print("⚠️ BookTicker Stream 연결 종료됨")
                            break

            except asyncio.CancelledError:
                print("⚠️ BookTicker Stream 취소됨")
                break
            except Exception as e:  # noqa: BLE001
                if self.running:
                    print(f"⚠️ BookTicker Stream 재연결 대기 중: {e}")
                    await asyncio.sleep(5)
                else:
                    break
            finally:
                if self._session:
                    await self._session.close()
                    self._session = None
                self._ws = None

    async def stop(self) -> None:
        """웹소켓 스트림 중지."""
        self.running = False
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None


