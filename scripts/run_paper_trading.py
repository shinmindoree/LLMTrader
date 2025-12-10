"""페이퍼 트레이딩 실행 스크립트."""

import asyncio
import json

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.paper.engine import PaperTradingEngine
from llmtrader.paper.price_feed import PriceFeed
from llmtrader.settings import get_settings
from llmtrader.strategy.examples.simple_ma import SimpleMAStrategy


async def main() -> None:
    """페이퍼 트레이딩 실행."""
    # 설정 로드
    settings = get_settings()

    # 바이낸스 클라이언트 생성
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )

    # 가격 피드 생성 (1초 간격 폴링)
    price_feed = PriceFeed(
        client=client,
        symbol="BTCUSDT",
        interval=1.0,
    )

    # 전략 생성
    strategy = SimpleMAStrategy(fast_period=5, slow_period=20, quantity=0.01)

    # 페이퍼 트레이딩 엔진 생성
    engine = PaperTradingEngine(
        strategy=strategy,
        price_feed=price_feed,
        initial_balance=10000.0,
        maker_fee=0.0002,
        taker_fee=0.0004,
        slippage=0.0001,
    )

    print("Starting paper trading... (Press Ctrl+C to stop)")
    print("=" * 80)

    try:
        # 엔진 시작
        await engine.start()
    except KeyboardInterrupt:
        print("\n" + "=" * 80)
        print("Paper trading stopped.")

    # 요약 통계 출력
    summary = engine.get_summary()
    print("\n=== Paper Trading Summary ===")
    print(json.dumps(summary, indent=2))

    # 클라이언트 종료
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())




