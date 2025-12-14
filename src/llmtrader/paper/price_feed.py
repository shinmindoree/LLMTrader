"""실시간 가격 피드."""

import asyncio
import time
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
        self._last_emitted_timestamp: int | None = None
        self._last_emitted_close: float = 0.0

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

    async def fetch_closed_closes(self, limit: int = 200) -> list[tuple[int, float]]:
        """최근 캔들 종가 히스토리(닫힌 봉) 조회.

        RSI/MA 등 지표가 시작부터 의미 있게 나오도록 price_history를 시딩(seed)할 때 사용.

        Returns:
            (timestamp_ms, close) 리스트. timestamp는 kline open time.
        """
        klines = await self.client.fetch_klines(symbol=self.symbol, interval="1m", limit=limit + 1)
        if not klines:
            return []

        # 일반적으로 마지막 원소는 진행 중인 현재 봉일 수 있으므로 제외(닫힌 봉만 사용)
        closed = klines[:-1] if len(klines) > 1 else klines
        out: list[tuple[int, float]] = []
        for k in closed:
            try:
                ts = int(k[0])
                close = float(k[4])
            except Exception:  # noqa: BLE001
                continue
            out.append((ts, close))
        return out

    async def start(self) -> None:
        """가격 피드 시작."""
        self._running = True
        while self._running:
            try:
                # 실시간 가격(체결가) - 거래소 화면과 최대한 동일하게 맞추기
                try:
                    last_price = await self.client.fetch_ticker_price(self.symbol)
                except Exception:  # noqa: BLE001
                    # ticker 실패 시 kline close로 fallback
                    last_price = self._last_price

                # 최근 1분봉 2개 조회
                # - bar_ts/close: 확정된 마지막 봉(= klines[-2])을 전략/지표에 사용
                # - last_price: 최신(미완성 포함) 가격(= klines[-1].close)을 마크가격/로그에 사용
                klines = await self.client.fetch_klines(
                    symbol=self.symbol,
                    interval="1m",
                    limit=2,
                )
                if klines:
                    recv_ts = int(time.time() * 1000)
                    safe_ts = recv_ts - 1500  # 네트워크/서버 지연 여유

                    # closeTime(k[6])을 기준으로 "닫힌 봉(last closed candle)"을 선택한다.
                    parsed: list[tuple[int, int, float]] = []
                    for k in klines:
                        try:
                            open_ts = int(k[0])
                            close_ts = int(k[6])
                            close_price = float(k[4])
                        except Exception:  # noqa: BLE001
                            continue
                        parsed.append((open_ts, close_ts, close_price))

                    parsed.sort(key=lambda x: x[0])
                    closed = [p for p in parsed if p[1] <= safe_ts]
                    if closed:
                        bar_ts, _, bar_close = closed[-1]
                    else:
                        bar_ts, _, bar_close = parsed[-1]

                    # ticker를 우선 사용, 없으면 bar_close 사용
                    if not last_price:
                        last_price = bar_close

                    # bar_ts가 과거로 되돌아가는 경우(노드/캐시 흔들림) 마지막 값으로 고정
                    if self._last_emitted_timestamp is not None and bar_ts < self._last_emitted_timestamp:
                        bar_ts = self._last_emitted_timestamp
                        bar_close = self._last_emitted_close

                    self._last_price = last_price

                    tick = {
                        # 로컬 수신 시각(로그/모니터링용)
                        "timestamp": recv_ts,
                        # 전략(1분봉) 기준
                        "bar_timestamp": bar_ts,
                        "bar_close": bar_close,
                        # 마크가격/로그/스탑로스 기준
                        "price": last_price,
                        "volume": float(klines[-1][5]) if klines else 0.0,
                    }

                    # 새 봉 여부
                    tick["is_new_bar"] = self._last_emitted_timestamp != bar_ts
                    if tick["is_new_bar"]:
                        self._last_emitted_timestamp = bar_ts
                        self._last_emitted_close = bar_close

                    # 콜백 호출
                    for callback in self._callbacks:
                        callback(tick)

            except Exception as exc:  # noqa: BLE001
                print(f"PriceFeed error: {exc}")

            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        """가격 피드 중지."""
        self._running = False




