"""바이낸스 API에서 BTC 1분봉 샘플 데이터 다운로드.

이 스크립트는 검증용 샘플 데이터(data/sample_btc_1m.csv)를 생성합니다.
최소 2주 분량(약 20,000 row)의 BTCUSDT 1분봉 데이터를 다운로드합니다.
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

# src 디렉토리를 Python 경로에 추가
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from backtest.data_fetcher import fetch_all_klines
from binance.client import BinanceHTTPClient
from settings import get_settings


async def download_sample_data(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    days: int = 14,
    output_path: Path | None = None,
) -> None:
    """BTC 1분봉 샘플 데이터 다운로드.

    Args:
        symbol: 거래 심볼 (기본값: BTCUSDT)
        interval: 캔들 간격 (기본값: 1m)
        days: 다운로드할 일수 (기본값: 14일, 약 20,000 row)
        output_path: 출력 파일 경로 (기본값: data/sample_btc_1m.csv)
    """
    if output_path is None:
        output_path = project_root / "data" / "sample_btc_1m.csv"

    # 출력 디렉토리 생성
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 설정 로드
    settings = get_settings()
    if not settings.binance.base_url:
        raise ValueError("BINANCE_BASE_URL이 설정되지 않았습니다.")

    # 바이낸스 클라이언트 생성 (API 키 없이도 공개 데이터 조회 가능)
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key or "",
        api_secret=settings.binance.api_secret or "",
        base_url=settings.binance.base_url,
    )

    try:
        # 시간 범위 계산 (현재 시점에서 days일 전)
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(days=days)

        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        print(f"📥 데이터 다운로드 시작: {symbol} {interval}")
        print(f"   기간: {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"   예상 일수: {days}일 (약 {days * 24 * 60}개 1분봉)")

        # 데이터 다운로드
        klines = await fetch_all_klines(
            client=client,
            symbol=symbol,
            interval=interval,
            start_ts=start_ts,
            end_ts=end_ts,
        )

        if not klines:
            print("❌ 데이터를 가져오지 못했습니다.")
            return

        # CSV 형식으로 변환
        # 바이낸스 klines 형식: [Open time, Open, High, Low, Close, Volume, Close time, ...]
        data = []
        for k in klines:
            data.append({
                "timestamp": int(k[0]),  # Open time (밀리초)
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        df = pd.DataFrame(data)

        # 타임스탬프를 기준으로 정렬 (오름차순)
        df = df.sort_values("timestamp").reset_index(drop=True)

        # CSV로 저장
        df.to_csv(output_path, index=False)

        print(f"✅ 데이터 다운로드 완료: {len(df)}개 행")
        print(f"   저장 경로: {output_path}")
        print(f"   파일 크기: {output_path.stat().st_size / 1024:.2f} KB")

        # 간단한 통계 출력
        print(f"\n📊 데이터 통계:")
        print(f"   시작 시간: {datetime.fromtimestamp(df['timestamp'].min() / 1000)}")
        print(f"   종료 시간: {datetime.fromtimestamp(df['timestamp'].max() / 1000)}")
        print(f"   가격 범위: ${df['low'].min():.2f} ~ ${df['high'].max():.2f}")
        print(f"   총 거래량: {df['volume'].sum():.2f}")

    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        raise
    finally:
        await client.aclose()


async def main() -> None:
    """메인 함수."""
    import argparse

    parser = argparse.ArgumentParser(description="BTC 1분봉 샘플 데이터 다운로드")
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="거래 심볼 (기본값: BTCUSDT)",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default="1m",
        help="캔들 간격 (기본값: 1m)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="다운로드할 일수 (기본값: 14일)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="출력 파일 경로 (기본값: data/sample_btc_1m.csv)",
    )

    args = parser.parse_args()

    output_path = Path(args.output) if args.output else None

    await download_sample_data(
        symbol=args.symbol,
        interval=args.interval,
        days=args.days,
        output_path=output_path,
    )


if __name__ == "__main__":
    asyncio.run(main())
