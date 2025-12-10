"""실시간 가격 피드."""

import asyncio
from typing import Any, Callable

from llmtrader.binance.client import BinanceHTTPClient


class PriceFeed:
    """실시간 가격 피드 (REST 폴링 기반)."""

    def __init__(
        self,
        client: BinanceHTTPClient,
        symbol: str,
        interval: float = 1.0,
    ) -> None:
        """가격 피드 초기화.

        Args:
            client: 바이낸스 HTTP 클라이언트
            symbol: 심볼 (예: BTCUSDT)
            interval: 폴링 간격 (초)
        """
        self.client = client
        self.symbol = symbol
        self.interval = interval
        self._running = False
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._last_price: float = 0.0

    @property
    def last_price(self) -> float:
        """마지막 가격."""
        return self._last_price

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """가격 업데이트 콜백 등록.

        Args:
            callback: 가격 업데이트 시 호출될 함수 (인자: tick 데이터)
        """
        self._callbacks.append(callback)

    async def start(self) -> None:
        """가격 피드 시작."""
        self._running = True
        while self._running:
            try:
                # 최근 1분봉 1개 조회
                klines = await self.client.fetch_klines(
                    symbol=self.symbol,
                    interval="1m",
                    limit=1,
                )
                if klines:
                    tick = {
                        "timestamp": klines[0][0],
                        "price": float(klines[0][4]),  # close price
                        "volume": float(klines[0][5]),
                    }
                    self._last_price = tick["price"]

                    # 콜백 호출
                    for callback in self._callbacks:
                        callback(tick)

            except Exception as exc:  # noqa: BLE001
                print(f"PriceFeed error: {exc}")

            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        """가격 피드 중지."""
        self._running = False




