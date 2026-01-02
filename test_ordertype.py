#!/usr/bin/env python3
"""Price 파라미터 기준 주문 타입 테스트.

사용법:
    uv run python test_ordertype.py
"""

import asyncio
import json
from typing import Any

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings


def determine_order_type(price: float | None) -> str:
    """주문 타입 결정 로직 (LiveContext와 동일).
    
    Args:
        price: 주문 가격 (None이면 MARKET, 있으면 LIMIT)
    
    Returns:
        "MARKET" 또는 "LIMIT"
    """
    return "MARKET" if price is None else "LIMIT"


async def test_order_type_by_price(
    symbol: str = "BTCUSDT",
    quantity: float = 0.001,
    test_market: bool = True,
    test_limit: bool = True,
) -> None:
    """Price 파라미터 기준 주문 타입 테스트.
    
    Args:
        symbol: 거래 심볼 (기본값: BTCUSDT)
        quantity: 주문 수량 (기본값: 0.001)
        test_market: MARKET 주문 테스트 여부
        test_limit: LIMIT 주문 테스트 여부
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
        print("Price 파라미터 기준 주문 타입 테스트")
        print("=" * 80)
        print(f"Symbol: {symbol}")
        print(f"Quantity: {quantity}")
        print(f"Base URL: {settings.binance.base_url}")
        print()
        
        # 현재 가격 조회
        print("📡 현재 가격 조회 중...")
        ticker = await client.fetch_ticker(symbol)
        current_price = float(ticker.get("lastPrice", 0))
        print(f"현재 가격: ${current_price:,.2f}")
        print()
        
        test_results: list[dict[str, Any]] = []
        
        # 1. MARKET 주문 테스트 (price=None)
        if test_market:
            print("-" * 80)
            print("테스트 1: MARKET 주문 (price=None)")
            print("-" * 80)
            
            expected_type = determine_order_type(None)
            print(f"예상 주문 타입: {expected_type}")
            print(f"주문 파라미터: price=None")
            print()
            
            print("⚠️  실제 주문을 제출합니다. 계속하시겠습니까? (y/n): ", end="")
            # 실제 테스트를 원하면 주석 해제
            # confirm = input().strip().lower()
            # if confirm != 'y':
            #     print("테스트 취소됨")
            #     return
            
            try:
                # MARKET 주문 제출
                response = await client.place_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    type="MARKET",
                )
                
                actual_type = response.get("type", "UNKNOWN")
                order_id = response.get("orderId")
                status = response.get("status", "UNKNOWN")
                
                print(f"\n✅ 주문 제출 성공!")
                print(f"주문 ID: {order_id}")
                print(f"응답의 type 필드: {actual_type}")
                print(f"주문 상태: {status}")
                print(f"예상 타입과 일치: {'✅' if actual_type == expected_type else '❌'}")
                
                test_results.append({
                    "test": "MARKET 주문",
                    "price": None,
                    "expected_type": expected_type,
                    "actual_type": actual_type,
                    "order_id": order_id,
                    "status": status,
                    "match": actual_type == expected_type,
                })
                
                # 주문 상세 조회
                if order_id:
                    print(f"\n📋 주문 상세 조회 중...")
                    await asyncio.sleep(1)  # 주문 처리 대기
                    order_detail = await client.fetch_order(symbol, int(order_id))
                    print(f"상세 조회 type: {order_detail.get('type', 'UNKNOWN')}")
                    print(f"상세 조회 status: {order_detail.get('status', 'UNKNOWN')}")
                    print(f"체결 수량: {order_detail.get('executedQty', '0')}")
                    print(f"원래 수량: {order_detail.get('origQty', '0')}")
                
            except Exception as e:
                print(f"\n❌ 오류 발생: {e}")
                import traceback
                traceback.print_exc()
            
            print()
        
        # 2. LIMIT 주문 테스트 (price 지정)
        if test_limit:
            print("-" * 80)
            print("테스트 2: LIMIT 주문 (price 지정)")
            print("-" * 80)
            
            # 현재가보다 낮은 가격으로 LIMIT 주문 (즉시 체결되지 않도록)
            limit_price = current_price * 0.95  # 현재가의 95%
            
            expected_type = determine_order_type(limit_price)
            print(f"예상 주문 타입: {expected_type}")
            print(f"주문 파라미터: price={limit_price:,.2f}")
            print()
            
            print("⚠️  실제 주문을 제출합니다. 계속하시겠습니까? (y/n): ", end="")
            # 실제 테스트를 원하면 주석 해제
            # confirm = input().strip().lower()
            # if confirm != 'y':
            #     print("테스트 취소됨")
            #     return
            
            try:
                # LIMIT 주문 제출
                response = await client.place_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    type="LIMIT",
                    price=limit_price,
                    timeInForce="GTC",
                )
                
                actual_type = response.get("type", "UNKNOWN")
                order_id = response.get("orderId")
                status = response.get("status", "UNKNOWN")
                
                print(f"\n✅ 주문 제출 성공!")
                print(f"주문 ID: {order_id}")
                print(f"응답의 type 필드: {actual_type}")
                print(f"주문 상태: {status}")
                print(f"예상 타입과 일치: {'✅' if actual_type == expected_type else '❌'}")
                
                test_results.append({
                    "test": "LIMIT 주문",
                    "price": limit_price,
                    "expected_type": expected_type,
                    "actual_type": actual_type,
                    "order_id": order_id,
                    "status": status,
                    "match": actual_type == expected_type,
                })
                
                # 주문 상세 조회
                if order_id:
                    print(f"\n📋 주문 상세 조회 중...")
                    await asyncio.sleep(1)  # 주문 처리 대기
                    order_detail = await client.fetch_order(symbol, int(order_id))
                    print(f"상세 조회 type: {order_detail.get('type', 'UNKNOWN')}")
                    print(f"상세 조회 status: {order_detail.get('status', 'UNKNOWN')}")
                    print(f"체결 수량: {order_detail.get('executedQty', '0')}")
                    print(f"원래 수량: {order_detail.get('origQty', '0')}")
                    print(f"주문 가격: {order_detail.get('price', '0')}")
                
            except Exception as e:
                print(f"\n❌ 오류 발생: {e}")
                import traceback
                traceback.print_exc()
            
            print()
        
        # 테스트 결과 요약
        print("=" * 80)
        print("테스트 결과 요약")
        print("=" * 80)
        for result in test_results:
            status_icon = "✅" if result["match"] else "❌"
            print(f"{status_icon} {result['test']}:")
            print(f"   Price: {result['price']}")
            print(f"   예상 타입: {result['expected_type']}")
            print(f"   실제 타입: {result['actual_type']}")
            print(f"   주문 ID: {result['order_id']}")
            print(f"   상태: {result['status']}")
            print()
        
        # JSON 출력
        print("=" * 80)
        print("전체 테스트 결과 (JSON):")
        print("-" * 80)
        print(json.dumps(test_results, indent=2, ensure_ascii=False))
        
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.aclose()


if __name__ == "__main__":
    # 실제 주문을 제출하지 않고 로직만 테스트하려면:
    # test_market=False, test_limit=False로 설정
    
    asyncio.run(test_order_type_by_price(
        symbol="BTCUSDT",
        quantity=0.001,  # 최소 주문 수량 확인 필요
        test_market=False,  # 실제 MARKET 주문 테스트 (True로 변경 시 실제 주문 제출)
        test_limit=False,  # 실제 LIMIT 주문 테스트 (True로 변경 시 실제 주문 제출)
    ))