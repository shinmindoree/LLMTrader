FROM python:3.12-slim

# 시간대 설정 (UTC)
ENV TZ=UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir uv
RUN uv sync

# 테스트넷 라이브 트레이딩 실행 (필요하면 전략/옵션 변경)
# 환경 변수로 설정 가능: CANDLE_INTERVAL (기본: 1m), LEVERAGE (기본: 10), MAX_POSITION (기본: 1.0), RSI_PERIOD (기본: 2), LOG_INTERVAL (기본: 0), STOPLOSS_COOLDOWN_CANDLES (기본: 50)
CMD ["sh", "-c", "uv run python scripts/run_live_trading.py rsi_long_short_strategy.py --symbol BTCUSDT --leverage ${LEVERAGE:-10} --candle-interval ${CANDLE_INTERVAL:-1m} --max-position ${MAX_POSITION:-1.0} --rsi-period ${RSI_PERIOD:-2} --log-interval ${LOG_INTERVAL:-0} --stoploss-cooldown-candles ${STOPLOSS_COOLDOWN_CANDLES:-50} --daily-loss-limit 500.0 --max-consecutive-losses 0 --yes"]
