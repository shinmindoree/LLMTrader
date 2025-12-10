"""히스토리컬 데이터 로더."""

from datetime import datetime, timedelta
from typing import Any

from llmtrader.binance.client import BinanceHTTPClient


class HistoricalDataLoader:
    """히스토리컬 캔들 데이터 로더."""

    def __init__(self, client: BinanceHTTPClient) -> None:
        """데이터 로더 초기화.

        Args:
            client: 바이낸스 HTTP 클라이언트
        """
        self.client = client

    async def load_klines(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """히스토리컬 캔들 데이터 로딩.

        Args:
            symbol: 심볼 (예: BTCUSDT)
            interval: 인터벌 (예: 1m, 5m, 1h, 1d)
            start_time: 시작 시간
            end_time: 종료 시간
            limit: 한 번에 가져올 최대 캔들 수 (기본 1000)

        Returns:
            캔들 데이터 리스트 [{timestamp, open, high, low, close, volume}, ...]
        """
        all_klines: list[dict[str, Any]] = []
        current_start = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        while current_start < end_ts:
            raw_klines = await self.client.fetch_klines(
                symbol=symbol,
                interval=interval,
                start_ts=current_start,
                end_ts=end_ts,
                limit=limit,
            )

            if not raw_klines:
                break

            # 원시 데이터를 파싱
            for kline in raw_klines:
                parsed = {
                    "timestamp": kline[0],
                    "open": float(kline[1]),
                    "high": float(kline[2]),
                    "low": float(kline[3]),
                    "close": float(kline[4]),
                    "volume": float(kline[5]),
                }
                all_klines.append(parsed)

            # 다음 구간으로 이동
            last_ts = raw_klines[-1][0]
            if last_ts >= end_ts:
                break
            current_start = last_ts + 1

        # end_time 이후 데이터 제거
        all_klines = [k for k in all_klines if k["timestamp"] <= end_ts]

        return all_klines

    async def load_klines_simple(
        self,
        symbol: str,
        interval: str,
        days: int = 30,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """최근 N일 캔들 데이터 로딩 (간편 메서드).

        Args:
            symbol: 심볼
            interval: 인터벌
            days: 최근 N일
            limit: 최대 캔들 수

        Returns:
            캔들 데이터 리스트
        """
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        return await self.load_klines(symbol, interval, start_time, end_time, limit)




