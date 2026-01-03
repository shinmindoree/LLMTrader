FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir uv
RUN uv sync --extra azure

# 테스트넷 라이브 트레이딩 실행 (필요하면 전략/옵션 변경)
# 환경 변수로 설정 가능: CANDLE_INTERVAL (기본: 1m), LEVERAGE (기본: 10), MAX_POSITION (기본: 1.0)
CMD ["sh", "-c", "uv run python scripts/run_live_trading.py rsi_long_short_strategy.py --symbol BTCUSDT --leverage ${LEVERAGE:-10} --interval 1.0 --candle-interval ${CANDLE_INTERVAL:-1m} --max-position ${MAX_POSITION:-1.0} --daily-loss-limit 500.0 --max-consecutive-losses 0 --yes"]
