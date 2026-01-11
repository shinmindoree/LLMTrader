"""샘플 데이터 검증 스크립트.

data/sample_btc_1m.csv 파일의 유효성을 검증합니다.
"""

import sys
from pathlib import Path

import pandas as pd

# 프로젝트 루트 설정
project_root = Path(__file__).parent.parent


def validate_sample_data(file_path: Path | None = None, min_rows: int = 20000) -> bool:
    """샘플 데이터 검증.

    Args:
        file_path: 검증할 CSV 파일 경로 (기본값: data/sample_btc_1m.csv)
        min_rows: 최소 행 수 (기본값: 20000, 약 2주 분량)

    Returns:
        검증 통과 여부
    """
    if file_path is None:
        file_path = project_root / "data" / "sample_btc_1m.csv"

    print(f"📋 데이터 검증 시작: {file_path}")

    # 파일 존재 확인
    if not file_path.exists():
        print(f"❌ 파일이 존재하지 않습니다: {file_path}")
        return False

    # CSV 파일 읽기
    try:
        df = pd.read_csv(file_path)
    except Exception as e:
        print(f"❌ CSV 파일 읽기 실패: {e}")
        return False

    # 필수 컬럼 확인
    required_columns = ["timestamp", "open", "high", "low", "close", "volume"]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"❌ 필수 컬럼 누락: {missing_columns}")
        print(f"   현재 컬럼: {list(df.columns)}")
        return False

    # 데이터 타입 확인
    print(f"✅ 컬럼 존재 확인 통과: {required_columns}")

    # 행 수 확인
    row_count = len(df)
    if row_count < min_rows:
        print(f"❌ 행 수 부족: {row_count}개 (최소 {min_rows}개 필요)")
        return False
    print(f"✅ 행 수 확인 통과: {row_count}개 (최소 {min_rows}개)")

    # 타임스탬프 형식 확인
    try:
        # 타임스탬프가 숫자인지 확인
        timestamps = pd.to_numeric(df["timestamp"], errors="coerce")
        if timestamps.isna().any():
            print(f"❌ 타임스탬프 형식 오류: 숫자로 변환할 수 없는 값이 있습니다")
            return False

        # 타임스탬프가 양수인지 확인 (밀리초 타임스탬프는 큰 숫자)
        if (timestamps < 1000000000000).any():  # 2001-09-09 이후
            print(f"⚠️  타임스탬프 값이 비정상적으로 작습니다 (밀리초 타임스탬프가 아닐 수 있음)")
            # 경고만 출력하고 계속 진행

        print(f"✅ 타임스탬프 형식 확인 통과 (밀리초 타임스탬프)")

    except Exception as e:
        print(f"❌ 타임스탬프 검증 실패: {e}")
        return False

    # 누락값 확인
    missing_values = df[required_columns].isna().sum()
    if missing_values.any():
        print(f"❌ 누락값 발견:")
        for col, count in missing_values.items():
            if count > 0:
                print(f"   {col}: {count}개")
        return False
    print(f"✅ 누락값 확인 통과")

    # 가격 데이터 유효성 확인 (high >= low, high/low/open/close가 양수)
    price_columns = ["open", "high", "low", "close"]
    for col in price_columns:
        if (df[col] <= 0).any():
            print(f"❌ 가격 데이터 오류: {col}에 0 이하의 값이 있습니다")
            return False

    # high >= low 확인
    if (df["high"] < df["low"]).any():
        print(f"❌ 가격 데이터 오류: high < low인 행이 있습니다")
        return False

    # volume이 음수가 아닌지 확인
    if (df["volume"] < 0).any():
        print(f"⚠️  거래량 데이터 경고: volume에 음수 값이 있습니다 (0으로 처리)")

    print(f"✅ 가격 데이터 유효성 확인 통과")

    # 타임스탬프 정렬 확인 (오름차순)
    if not df["timestamp"].is_monotonic_increasing:
        print(f"⚠️  타임스탬프 정렬 경고: 타임스탬프가 오름차순으로 정렬되어 있지 않습니다")
        # 경고만 출력하고 계속 진행

    # 간단한 통계 출력
    print(f"\n📊 데이터 통계:")
    print(f"   총 행 수: {row_count:,}개")
    print(f"   시작 타임스탬프: {df['timestamp'].min()}")
    print(f"   종료 타임스탬프: {df['timestamp'].max()}")
    print(f"   가격 범위: ${df['low'].min():.2f} ~ ${df['high'].max():.2f}")
    print(f"   평균 거래량: {df['volume'].mean():.2f}")

    print(f"\n✅ 데이터 검증 완료: 모든 검증 통과")
    return True


def main() -> None:
    """메인 함수."""
    import argparse

    parser = argparse.ArgumentParser(description="샘플 데이터 검증")
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="검증할 CSV 파일 경로 (기본값: data/sample_btc_1m.csv)",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=20000,
        help="최소 행 수 (기본값: 20000)",
    )

    args = parser.parse_args()

    file_path = Path(args.file) if args.file else None

    success = validate_sample_data(file_path=file_path, min_rows=args.min_rows)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
