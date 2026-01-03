import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# -----------------------------------------------------------
# 1. 사용자 입력 (원하는 날짜 설정)
# -----------------------------------------------------------
target_date_str = "2025-04-24"  # 형식: YYYY-MM-DD

# -----------------------------------------------------------
# 2. 날짜를 타임스탬프(ms)로 변환하는 로직
# -----------------------------------------------------------
# 문자열을 datetime 객체로 변환 (UTC 기준 00:00:00)
target_date = datetime.strptime(target_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

# 시작 시간 (해당 날짜 00:00:00) -> 밀리초 단위 변환
start_time = int(target_date.timestamp() * 1000)

# 종료 시간 (해당 날짜 23:59:59) -> 시작 시간 + 24시간 - 1밀리초
end_time = start_time + (24 * 60 * 60 * 1000) - 1

# -----------------------------------------------------------
# 3. API 요청
# -----------------------------------------------------------
url = "https://api.binance.com/api/v3/klines"

params = {
    'symbol': 'BTCUSDT',
    'interval': '1d',     # 특정 '일자'의 데이터이므로 1일(1d) 간격 사용
    'startTime': start_time,
    'endTime': end_time,
    'limit': 1            # 하루치 데이터만 필요하므로 1개
}

response = requests.get(url, params=params)
data = response.json()

# -----------------------------------------------------------
# 4. 데이터프레임 변환 및 출력
# -----------------------------------------------------------
if not data:
    print(f"{target_date_str}의 데이터가 존재하지 않습니다.")
else:
    df = pd.DataFrame(data, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])

    # 필요한 컬럼만 선택
    df = df[['open_time', 'open', 'high', 'low', 'close', 'volume']]

    # 시간 변환 (보기 편하게)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    
    # 숫자형 변환
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)

    print(f"=== {target_date_str} BTC/USDT OHLCV ===")
    print(df)