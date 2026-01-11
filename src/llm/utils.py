"""LLM 모듈 유틸리티 - 데이터 로더 및 Resampling."""

from pathlib import Path

import pandas as pd


def interval_to_minutes(interval: str) -> int:
    """타임프레임 문자열을 분 단위로 변환.

    Args:
        interval: 타임프레임 문자열 (예: "1m", "15m", "1h", "4h", "1d")

    Returns:
        분 단위 값

    Examples:
        >>> interval_to_minutes("1m")
        1
        >>> interval_to_minutes("15m")
        15
        >>> interval_to_minutes("1h")
        60
        >>> interval_to_minutes("4h")
        240
        >>> interval_to_minutes("1d")
        1440
    """
    interval = interval.lower().strip()

    if interval.endswith("m"):
        return int(interval[:-1])
    elif interval.endswith("h"):
        return int(interval[:-1]) * 60
    elif interval.endswith("d"):
        return int(interval[:-1]) * 1440
    elif interval.endswith("w"):
        return int(interval[:-1]) * 10080
    elif interval.endswith("M"):
        # 월 단위는 대략 30일로 계산
        return int(interval[:-1]) * 43200
    else:
        raise ValueError(f"지원하지 않는 타임프레임 형식: {interval}")


def validate_interval(interval: str) -> bool:
    """타임프레임 유효성 검증.

    Args:
        interval: 타임프레임 문자열

    Returns:
        유효 여부
    """
    try:
        minutes = interval_to_minutes(interval)
        return minutes > 0
    except Exception:
        return False


def load_and_resample_data(filepath: Path, target_interval: str) -> pd.DataFrame:
    """CSV 파일을 로드하고 타겟 타임프레임으로 Resampling.

    Args:
        filepath: CSV 파일 경로 (컬럼: timestamp, open, high, low, close, volume)
        target_interval: 타겟 타임프레임 (예: "1m", "15m", "4h")

    Returns:
        Resampling된 DataFrame (컬럼: timestamp, open, high, low, close, volume)

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때
        ValueError: 타임프레임이 유효하지 않을 때
    """
    if not filepath.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {filepath}")

    if not validate_interval(target_interval):
        raise ValueError(f"유효하지 않은 타임프레임: {target_interval}")

    # CSV 파일 로드
    df = pd.read_csv(filepath)

    # 필수 컬럼 확인
    required_columns = ["timestamp", "open", "high", "low", "close", "volume"]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"필수 컬럼 누락: {missing_columns}")

    # 타임스탬프를 datetime으로 변환
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    # 타임스탬프를 인덱스로 설정
    df = df.set_index("timestamp")

    # 타겟 타임프레임으로 Resampling
    # pandas의 resample 규칙: "1T" = 1분, "15T" = 15분, "1H" = 1시간, "1D" = 1일
    resample_rule = _interval_to_pandas_rule(target_interval)

    # OHLCV 리샘플링
    # open: 첫 번째 값
    # high: 최대값
    # low: 최소값
    # close: 마지막 값
    # volume: 합계
    resampled = df.resample(resample_rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )

    # NaN 값 제거 (데이터가 없는 구간)
    resampled = resampled.dropna()

    # 인덱스를 다시 컬럼으로 변환
    resampled = resampled.reset_index()

    # 타임스탬프를 밀리초로 변환
    resampled["timestamp"] = resampled["timestamp"].astype("int64") // 1_000_000

    return resampled


def _interval_to_pandas_rule(interval: str) -> str:
    """타임프레임 문자열을 pandas resample 규칙으로 변환.

    Args:
        interval: 타임프레임 문자열 (예: "1m", "15m", "1h", "4h", "1d")

    Returns:
        pandas resample 규칙 (예: "1T", "15T", "1H", "4H", "1D")
    """
    interval = interval.lower().strip()

    if interval.endswith("m"):
        minutes = int(interval[:-1])
        return f"{minutes}min"  # min = minutes (T는 deprecated)
    elif interval.endswith("h"):
        hours = int(interval[:-1])
        return f"{hours}H"  # H = hours
    elif interval.endswith("d"):
        days = int(interval[:-1])
        return f"{days}D"  # D = days
    elif interval.endswith("w"):
        weeks = int(interval[:-1])
        return f"{weeks}W"  # W = weeks
    elif interval.endswith("M"):
        months = int(interval[:-1])
        return f"{months}M"  # M = months
    else:
        raise ValueError(f"지원하지 않는 타임프레임 형식: {interval}")
