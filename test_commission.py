#!/usr/bin/env python3
"""Commission Rate 조회 테스트.

사용법:
    uv run python test.py
"""

import asyncio
import json

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings


async def test_commission_rate(symbol: str = "BTCUSDT") -> None:
    """Commission Rate 조회 테스트.
    
    Args:
        symbol: 거래 심볼 (기본값: BTCUSDT)
    """
    settings = get_settings()
    
    if not settings.binance.api_key or not settings.binance.api_secret:
        print("❌ 환경 변수 BINANCE_API_KEY, BINANCE_API_SECRET이 설정되지 않았습니다.")
        return
    
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )
    
    try:
        print("=" * 80)
        print(f"Commission Rate 조회 테스트")
        print("=" * 80)
        print(f"Symbol: {symbol}")
        print(f"Base URL: {settings.binance.base_url}")
        print()
        
        # Commission Rate 조회
        print("📡 Commission Rate 조회 중...")
        commission_rate_info = await client.fetch_commission_rate(symbol)
        
        print("\n✅ Commission Rate 조회 성공!")
        print("\n응답 데이터:")
        print("-" * 80)
        
        # 주요 필드 출력
        print(f"symbol: {commission_rate_info.get('symbol')}")
        print(f"makerCommissionRate: {commission_rate_info.get('makerCommissionRate')} ({float(commission_rate_info.get('makerCommissionRate', '0')) * 100:.4f}%)")
        print(f"takerCommissionRate: {commission_rate_info.get('takerCommissionRate')} ({float(commission_rate_info.get('takerCommissionRate', '0')) * 100:.4f}%)")
        print(f"rpiCommissionRate: {commission_rate_info.get('rpiCommissionRate')} ({float(commission_rate_info.get('rpiCommissionRate', '0')) * 100:.4f}%)")
        
        # 전체 응답 출력 (JSON)
        print("\n" + "=" * 80)
        print("전체 응답 (JSON):")
        print("-" * 80)
        print(json.dumps(commission_rate_info, indent=2, ensure_ascii=False))
        
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(test_commission_rate())