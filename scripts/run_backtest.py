"""백테스트 실행 스크립트."""

import asyncio
import json
from datetime import datetime, timedelta

from llmtrader.backtest.data_loader import HistoricalDataLoader
from llmtrader.backtest.engine import BacktestEngine
from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings
from llmtrader.strategy.examples.simple_ma import SimpleMAStrategy


async def main() -> None:
    """백테스트 실행."""
    # 설정 로드
    settings = get_settings()

    # 바이낸스 클라이언트 생성
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )

    # 데이터 로더
    loader = HistoricalDataLoader(client)

    # 히스토리컬 데이터 로딩 (최근 7일, 1시간봉)
    print("Loading historical data...")
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=7)
    klines = await loader.load_klines(
        symbol="BTCUSDT",
        interval="1h",
        start_time=start_time,
        end_time=end_time,
    )
    print(f"Loaded {len(klines)} candles")

    # 전략 생성
    strategy = SimpleMAStrategy(fast_period=5, slow_period=20, quantity=0.01)

    # 백테스트 실행
    print("Running backtest...")
    engine = BacktestEngine(
        strategy=strategy,
        initial_balance=10000.0,
        maker_fee=0.0002,
        taker_fee=0.0004,
        slippage=0.0001,
    )
    result = engine.run(klines)

    # 결과 출력
    print("\n=== Backtest Results ===")
    print(json.dumps(result, indent=2))

    # 클라이언트 종료
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())




