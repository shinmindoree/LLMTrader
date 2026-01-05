"""백테스트용 과거 데이터 수집."""

import asyncio
from typing import Any, Callable

from binance.client import BinanceHTTPClient


async def fetch_all_klines(
    client: BinanceHTTPClient,
    symbol: str,
    interval: str,
    start_ts: int,
    end_ts: int,
    batch_size: int = 1500,
    progress_callback: Callable[[float], None] | None = None,
) -> list[list[Any]]:
    """전체 기간의 캔들 데이터를 여러 번 API 호출로 가져옵니다.
    
    바이낸스 API는 한 번에 최대 1500개만 가져올 수 있으므로,
    시작 시간부터 종료 시간까지 모든 데이터를 가져오기 위해
    여러 번 호출합니다.
    
    Args:
        client: 바이낸스 클라이언트
        symbol: 거래 심볼
        interval: 캔들 간격
        start_ts: 시작 타임스탬프 (밀리초)
        end_ts: 종료 타임스탬프 (밀리초)
        batch_size: 한 번에 가져올 최대 개수 (기본 1500, API 최대값)
        progress_callback: 진행률 콜백 함수 (0.0 ~ 100.0)
    
    Returns:
        전체 기간의 캔들 데이터 리스트
    """
    all_klines: list[list[Any]] = []
    current_start_ts = start_ts
    max_iterations = 10000  # 무한 루프 방지
    
    print(f"📥 과거 데이터 수집 시작: {symbol} {interval}")
    print(f"   기간: {start_ts} ~ {end_ts}")
    
    # 전체 기간 추정 (진행률 계산용)
    total_duration = end_ts - start_ts
    
    for iteration in range(max_iterations):
        # 한 번에 최대 batch_size개씩 조회
        klines = await client.fetch_klines(
            symbol=symbol,
            interval=interval,
            start_ts=current_start_ts,
            end_ts=end_ts,
            limit=batch_size,
        )
        
        if not klines:
            break
        
        # 중복 제거: 이전 배치의 마지막과 겹칠 수 있으므로 확인
        if all_klines:
            last_ts = all_klines[-1][0]
            klines = [k for k in klines if k[0] > last_ts]
        
        if not klines:
            break
        
        all_klines.extend(klines)
        
        # 마지막 캔들의 종료 시간 + 1ms를 다음 시작 시간으로 설정
        last_close_time = int(klines[-1][6])
        
        # 종료 조건 확인
        if last_close_time >= end_ts:
            break
        
        if len(klines) < batch_size:
            # 요청한 개수보다 적게 왔으면 끝
            break
        
        # 다음 배치의 시작 시간
        current_start_ts = last_close_time + 1
        
        # 진행률 계산 및 업데이트
        if total_duration > 0:
            elapsed_duration = last_close_time - start_ts
            progress = min(100.0, (elapsed_duration / total_duration) * 100)
            if progress_callback:
                progress_callback(progress)
        
        # 진행 상황 출력
        if (iteration + 1) % 10 == 0:
            print(f"   진행 중... {len(all_klines)}개 수집됨")
        
        # API 레이트 리밋을 피하기 위해 약간 대기
        await asyncio.sleep(0.1)
    
    # 최종 진행률 100%로 설정
    if progress_callback:
        progress_callback(100.0)
    
    print(f"✅ 데이터 수집 완료: 총 {len(all_klines)}개 캔들")
    return all_klines

