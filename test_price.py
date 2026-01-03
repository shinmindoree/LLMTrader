"""바이낸스 특정 날짜 OHLCV 데이터 조회 테스트."""

import asyncio
from datetime import datetime, timezone

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings


async def fetch_date_klines(
    date_str: str,
    symbol: str = "BTCUSDT",
    interval: str = "1d",
) -> None:
    """특정 날짜의 OHLCV 데이터를 조회합니다.
    
    Args:
        date_str: 날짜 문자열 (예: "2024-01-01" 또는 "2024-01-01 00:00")
        symbol: 거래 심볼 (기본: BTCUSDT)
        interval: 캔들 간격 (기본: 1d) - 소문자로 입력 (1m, 5m, 1h, 1d 등)
    """
    # Interval 정규화 (대문자를 소문자로 변환)
    interval = interval.lower()
    
    # 설정 로드
    settings = get_settings()
    
    # 클라이언트 생성
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )
    
    try:
        # 날짜 파싱
        try:
            # "YYYY-MM-DD" 형식
            if len(date_str) == 10:
                target_date = datetime.strptime(date_str, "%Y-%m-%d")
            # "YYYY-MM-DD HH:MM" 형식
            elif len(date_str) == 16:
                target_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
            else:
                print(f"❌ 날짜 형식 오류: {date_str}")
                print("   지원 형식: 'YYYY-MM-DD' 또는 'YYYY-MM-DD HH:MM'")
                return
        except ValueError as e:
            print(f"❌ 날짜 파싱 오류: {e}")
            return
        
        # UTC 기준으로 변환 (바이낸스는 UTC 사용)
        target_date = target_date.replace(tzinfo=timezone.utc)
        
        # 해당 날짜의 시작 시간과 종료 시간 계산
        start_time = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)
        
        print(f"📅 데이터 조회 중...")
        print(f"   심볼: {symbol}")
        print(f"   간격: {interval} (정규화됨)")
        print(f"   날짜: {target_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"   시작: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"   종료: {end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()
        
        # 데이터 조회 (바이낸스는 한 번에 최대 1500개)
        all_klines = []
        current_start_ts = start_ts
        max_iterations = 100  # 무한 루프 방지
        
        for iteration in range(max_iterations):
            try:
                klines = await client.fetch_klines(
                    symbol=symbol,
                    interval=interval,
                    start_ts=current_start_ts,
                    end_ts=end_ts,
                    limit=1500,  # 바이낸스 최대값
                )
                
                # 🔍 디버깅: 첫 번째 응답의 원시 데이터 출력
                if iteration == 0 and klines:
                    print("=" * 80)
                    print("🔍 원시 API 응답 (첫 번째 캔들)")
                    print("=" * 80)
                    first_kline = klines[0]
                    print(f"전체 배열 길이: {len(first_kline)}")
                    print(f"원시 데이터: {first_kline}")
                    print()
                    print("인덱스별 값:")
                    for i, val in enumerate(first_kline):
                        print(f"  [{i}] = {val} (타입: {type(val).__name__})")
                    print()
                    print("바이낸스 표준 형식:")
                    print("  [0] = Open time (ms)")
                    print("  [1] = Open price")
                    print("  [2] = High price")
                    print("  [3] = Low price")
                    print("  [4] = Close price")
                    print("  [5] = Volume")
                    print("  [6] = Close time (ms)")
                    print("=" * 80)
                    print()
                
            except Exception as e:
                # 에러 응답 바디 확인
                error_msg = str(e)
                if "400 Bad Request" in error_msg:
                    print(f"❌ API 오류: {error_msg}")
                    print(f"   확인사항:")
                    print(f"   - Interval 형식이 올바른지 확인 (소문자: 1m, 5m, 1h, 1d 등)")
                    print(f"   - 테스트넷에는 해당 날짜 데이터가 없을 수 있습니다")
                    print(f"   - 실서버(base_url 변경)로 시도해보세요")
                raise
            
            if not klines:
                break
            
            # 중복 제거 (이전에 가져온 데이터와 겹치는 경우)
            if all_klines:
                last_ts = all_klines[-1][0]
                klines = [k for k in klines if k[0] > last_ts]
            
            if not klines:
                break
            
            all_klines.extend(klines)
            
            # 마지막 캔들의 종료 시간 확인
            last_close_time = int(klines[-1][6])
            
            # 더 이상 데이터가 없거나 종료 시간을 넘었으면 중단
            if len(klines) < 1500 or last_close_time >= end_ts:
                break
            
            # 다음 배치 시작 시간 설정
            current_start_ts = last_close_time + 1
        
        if not all_klines:
            print("❌ 해당 날짜에 데이터가 없습니다.")
            return
        
        print(f"✅ 총 {len(all_klines)}개의 캔들 데이터를 가져왔습니다.\n")
        
        # 데이터 요약 정보
        print("=" * 80)
        print("📊 데이터 요약")
        print("=" * 80)
        
        first_kline = all_klines[0]
        last_kline = all_klines[-1]
        
        first_time = datetime.fromtimestamp(first_kline[0] / 1000, tz=timezone.utc)
        last_time = datetime.fromtimestamp(last_kline[6] / 1000, tz=timezone.utc)
        
        print(f"첫 캔들: {first_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"마지막 캔들: {last_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print()
        
        # 가격 통계
        opens = [float(k[1]) for k in all_klines]
        highs = [float(k[2]) for k in all_klines]
        lows = [float(k[3]) for k in all_klines]
        closes = [float(k[4]) for k in all_klines]
        volumes = [float(k[5]) for k in all_klines]
        
        print(f"시가 범위: {min(opens):,.2f} ~ {max(opens):,.2f}")
        print(f"고가 범위: {min(highs):,.2f} ~ {max(highs):,.2f}")
        print(f"저가 범위: {min(lows):,.2f} ~ {max(lows):,.2f}")
        print(f"종가 범위: {min(closes):,.2f} ~ {max(closes):,.2f}")
        print(f"총 거래량: {sum(volumes):,.4f}")
        print()
        
        # 🔍 추가 검증: 각 캔들의 데이터 일관성 체크
        print("=" * 80)
        print("🔍 데이터 일관성 검증")
        print("=" * 80)
        for i, kline in enumerate(all_klines[:5]):  # 처음 5개만 상세 출력
            open_time = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc)
            open_price = float(kline[1])
            high_price = float(kline[2])
            low_price = float(kline[3])
            close_price = float(kline[4])
            
            print(f"\n캔들 {i+1} ({open_time.strftime('%Y-%m-%d %H:%M:%S UTC')}):")
            print(f"  Open:  {open_price:,.2f}")
            print(f"  High:  {high_price:,.2f}")
            print(f"  Low:   {low_price:,.2f}")
            print(f"  Close: {close_price:,.2f}")
            
            # 검증
            if high_price < low_price:
                print(f"  ⚠️ 경고: High < Low (데이터 오류 가능)")
            if open_price > high_price or open_price < low_price:
                print(f"  ⚠️ 경고: Open이 High/Low 범위 밖")
            if close_price > high_price or close_price < low_price:
                print(f"  ⚠️ 경고: Close가 High/Low 범위 밖")
            if abs(high_price - low_price) / low_price > 0.5:  # 50% 이상 변동
                print(f"  ⚠️ 경고: 가격 변동폭이 50% 이상 (비정상적일 수 있음)")
        
        print()
        
        # 처음 10개와 마지막 10개 출력
        print("=" * 80)
        print("📋 처음 10개 캔들")
        print("=" * 80)
        print(f"{'시간':<20} {'Open':>12} {'High':>12} {'Low':>12} {'Close':>12} {'Volume':>15}")
        print("-" * 80)
        
        for kline in all_klines[:10]:
            open_time = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc)
            open_price = float(kline[1])
            high_price = float(kline[2])
            low_price = float(kline[3])
            close_price = float(kline[4])
            volume = float(kline[5])
            
            print(
                f"{open_time.strftime('%Y-%m-%d %H:%M:%S'):<20} "
                f"{open_price:>12,.2f} "
                f"{high_price:>12,.2f} "
                f"{low_price:>12,.2f} "
                f"{close_price:>12,.2f} "
                f"{volume:>15,.4f}"
            )
        
        print()
        print("=" * 80)
        print("📋 마지막 10개 캔들")
        print("=" * 80)
        print(f"{'시간':<20} {'Open':>12} {'High':>12} {'Low':>12} {'Close':>12} {'Volume':>15}")
        print("-" * 80)
        
        for kline in all_klines[-10:]:
            open_time = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc)
            open_price = float(kline[1])
            high_price = float(kline[2])
            low_price = float(kline[3])
            close_price = float(kline[4])
            volume = float(kline[5])
            
            print(
                f"{open_time.strftime('%Y-%m-%d %H:%M:%S'):<20} "
                f"{open_price:>12,.2f} "
                f"{high_price:>12,.2f} "
                f"{low_price:>12,.2f} "
                f"{close_price:>12,.2f} "
                f"{volume:>15,.4f}"
            )
        
        # 데이터 이상 여부 체크
        print()
        print("=" * 80)
        print("🔍 데이터 이상 여부 체크")
        print("=" * 80)
        
        issues = []
        
        # 1. High < Low 체크
        for i, kline in enumerate(all_klines):
            high = float(kline[2])
            low = float(kline[3])
            if high < low:
                open_time = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc)
                issues.append(f"캔들 {i+1} ({open_time}): High({high}) < Low({low})")
        
        # 2. Open/Close가 High/Low 범위 밖인지 체크
        for i, kline in enumerate(all_klines):
            open_price = float(kline[1])
            high = float(kline[2])
            low = float(kline[3])
            close = float(kline[4])
            
            open_time = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc)
            
            if open_price > high or open_price < low:
                issues.append(
                    f"캔들 {i+1} ({open_time}): Open({open_price})가 High({high})/Low({low}) 범위 밖"
                )
            
            if close > high or close < low:
                issues.append(
                    f"캔들 {i+1} ({open_time}): Close({close})가 High({high})/Low({low}) 범위 밖"
                )
        
        # 3. 음수 값 체크
        for i, kline in enumerate(all_klines):
            open_price = float(kline[1])
            high = float(kline[2])
            low = float(kline[3])
            close = float(kline[4])
            volume = float(kline[5])
            
            if any(x < 0 for x in [open_price, high, low, close, volume]):
                open_time = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc)
                issues.append(f"캔들 {i+1} ({open_time}): 음수 값 발견")
        
        # 4. 비정상적인 가격 변동폭 체크 (50% 이상)
        for i, kline in enumerate(all_klines):
            high = float(kline[2])
            low = float(kline[3])
            if low > 0:
                price_range_pct = ((high - low) / low) * 100
                if price_range_pct > 50:
                    open_time = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc)
                    issues.append(
                        f"캔들 {i+1} ({open_time}): 가격 변동폭 {price_range_pct:.1f}% (비정상적)"
                    )
        
        if issues:
            print(f"⚠️ {len(issues)}개의 이상 데이터 발견:")
            for issue in issues[:20]:  # 최대 20개만 출력
                print(f"   - {issue}")
            if len(issues) > 20:
                print(f"   ... 외 {len(issues) - 20}개 더")
        else:
            print("✅ 이상 데이터 없음")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.aclose()


async def main():
    """메인 함수 - 날짜 입력받아서 데이터 조회."""
    print("=" * 80)
    print("바이낸스 특정 날짜 OHLCV 데이터 조회")
    print("=" * 80)
    print()
    
    # 날짜 입력
    date_str = input("날짜를 입력하세요 (예: 2024-01-01 또는 2024-01-01 00:00): ").strip()
    
    if not date_str:
        print("❌ 날짜를 입력해주세요.")
        return
    
    # 심볼 입력 (선택사항)
    symbol_input = input("심볼을 입력하세요 (기본: BTCUSDT, Enter로 기본값 사용): ").strip()
    symbol = symbol_input if symbol_input else "BTCUSDT"
    
    # 간격 입력 (선택사항) - 소문자로 정규화
    interval_input = input("캔들 간격을 입력하세요 (기본: 1d, Enter로 기본값 사용): ").strip()
    interval = interval_input.lower() if interval_input else "1d"  # 소문자로 변환
    
    print()
    
    await fetch_date_klines(date_str, symbol=symbol, interval=interval)


if __name__ == "__main__":
    asyncio.run(main())
    