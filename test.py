"""바이낸스 선물 과거 데이터 조회 테스트."""

import asyncio
from datetime import datetime, timedelta

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings


async def main():
    # 설정 로드
    settings = get_settings()
    
    # 클라이언트 생성
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )
    
    try:
        # 최근 7일간의 1시간봉 데이터 조회
        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
        start_ts = int(start_time.timestamp() * 1000)
        
        print(f"데이터 조회 중...")
        print(f"심볼: BTCUSDT")
        print(f"간격: 1h")
        print(f"기간: {start_time.strftime('%Y-%m-%d')} ~ {end_time.strftime('%Y-%m-%d')}")
        print()
        
        klines = await client.fetch_klines(
            symbol="BTCUSDT",
            interval="1m",
            start_ts=start_ts,
            limit=500,
        )
        
        if not klines:
            print("데이터가 없습니다.")
            return
        
        print(f"총 {len(klines)}개의 캔들 데이터를 가져왔습니다.\n")
        
        # 처음 5개와 마지막 5개 출력
        print("=== 처음 5개 ===")
        for i, kline in enumerate(klines[:5]):
            open_time = datetime.fromtimestamp(kline[0] / 1000)
            print(
                f"{i+1}. {open_time.strftime('%Y-%m-%d %H:%M')} | "
                f"O: {float(kline[1]):,.2f} | "
                f"H: {float(kline[2]):,.2f} | "
                f"L: {float(kline[3]):,.2f} | "
                f"C: {float(kline[4]):,.2f} | "
                f"V: {float(kline[5]):,.4f}"
            )
        
        print("\n=== 마지막 5개 ===")
        for i, kline in enumerate(klines[-5:], start=len(klines)-4):
            open_time = datetime.fromtimestamp(kline[0] / 1000)
            print(
                f"{i}. {open_time.strftime('%Y-%m-%d %H:%M')} | "
                f"O: {float(kline[1]):,.2f} | "
                f"H: {float(kline[2]):,.2f} | "
                f"L: {float(kline[3]):,.2f} | "
                f"C: {float(kline[4]):,.2f} | "
                f"V: {float(kline[5]):,.4f}"
            )
        
        # 요약 정보
        first = klines[0]
        last = klines[-1]
        closes = [float(k[4]) for k in klines]
        
        print("\n=== 요약 정보 ===")
        print(f"시작가: {float(first[1]):,.2f}")
        print(f"종료가: {float(last[4]):,.2f}")
        print(f"최고가: {max(float(k[2]) for k in klines):,.2f}")
        print(f"최저가: {min(float(k[3]) for k in klines):,.2f}")
        print(f"변동률: {((float(last[4]) / float(first[1])) - 1) * 100:.2f}%")
        
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())