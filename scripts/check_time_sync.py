"""Binance 서버 시간과 로컬 시간 동기화 상태 확인 스크립트."""
import asyncio
import time
import httpx
from datetime import datetime

from llmtrader.settings import get_settings


async def check_time_sync(base_url: str = "https://testnet.binancefuture.com"):
    """Binance 서버 시간과 로컬 시간 동기화 상태 확인.

    Args:
        base_url: Binance API 베이스 URL

    Returns:
        동기화 상태가 양호하면 True, 그렇지 않으면 False
    """
    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=10.0
        ) as client:
            response = await client.get("/fapi/v1/time")
            response.raise_for_status()
            data = response.json()

            server_time_ms = data["serverTime"]
            local_time_ms = int(time.time() * 1000)
            diff_ms = abs(server_time_ms - local_time_ms)

            server_time = datetime.fromtimestamp(server_time_ms / 1000)
            local_time = datetime.fromtimestamp(local_time_ms / 1000)

            print(f"✅ Binance 서버 시간: {server_time} ({server_time_ms})")
            print(f"✅ 로컬 시간: {local_time} ({local_time_ms})")
            print(f"✅ 시간 차이: {diff_ms}ms ({diff_ms/1000:.2f}초)")

            if diff_ms > 10000:
                print(f"⚠️ 경고: 시간 차이가 10초 이상입니다! (차이: {diff_ms}ms)")
                print("   Binance API 호출 시 오류가 발생할 수 있습니다.")
                return False
            elif diff_ms > 5000:
                print(f"⚠️ 주의: 시간 차이가 5초 이상입니다. (차이: {diff_ms}ms)")
                print("   recvWindow=60000 설정으로 대부분의 경우 문제없이 동작합니다.")
                return True
            else:
                print("✅ 시간 동기화 상태 양호")
                return True

    except httpx.TimeoutException:
        print("❌ 시간 동기화 확인 실패: 네트워크 타임아웃")
        return False
    except Exception as e:
        print(f"❌ 시간 동기화 확인 실패: {e}")
        return False


async def main():
    """메인 함수."""
    settings = get_settings()
    
    # 설정에서 base_url 가져오기
    base_url = settings.binance.base_url
    
    print("=" * 60)
    print("Binance 서버 시간 동기화 확인")
    print("=" * 60)
    print(f"API 엔드포인트: {base_url}")
    print()
    
    success = await check_time_sync(base_url)
    
    print()
    print("=" * 60)
    
    if success:
        print("✅ 시간 동기화 상태 확인 완료")
        exit(0)
    else:
        print("❌ 시간 동기화 문제가 감지되었습니다")
        print("   Dockerfile의 시간대 설정과 recvWindow 설정을 확인하세요.")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())

