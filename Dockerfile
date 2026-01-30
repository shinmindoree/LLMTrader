FROM python:3.12-slim

# 시간대 설정 (UTC)
ENV TZ=UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app
COPY . /app

ENV PYTHONPATH=/app/src

RUN pip install --no-cache-dir uv
RUN uv sync

# 테스트넷 라이브 트레이딩 실행 (필요하면 전략/옵션 변경)
# 환경 변수로 설정 가능:
# - STREAMS (JSON, 우선 적용)
# - SYMBOL, CANDLE_INTERVAL, LEVERAGE, MAX_POSITION, DAILY_LOSS_LIMIT, MAX_CONSECUTIVE_LOSSES, STOPLOSS_COOLDOWN_CANDLES, STOP_LOSS_PCT (STREAMS 미설정 시 기본값 생성)
# - STRATEGY_PARAMS (JSON), INDICATOR_CONFIG (JSON), LOG_INTERVAL (기본: 0)
CMD ["sh", "-c", "STREAMS_JSON=$(printf '[{\"symbol\":\"%s\",\"interval\":\"%s\",\"leverage\":%s,\"max_position\":%s,\"daily_loss_limit\":%s,\"max_consecutive_losses\":%s,\"stop_loss_pct\":%s,\"stoploss_cooldown_candles\":%s}]' \"${SYMBOL:-ETHUSDT}\" \"${CANDLE_INTERVAL:-1m}\" \"${LEVERAGE:-10}\" \"${MAX_POSITION:-1.0}\" \"${DAILY_LOSS_LIMIT:-500}\" \"${MAX_CONSECUTIVE_LOSSES:-0}\" \"${STOP_LOSS_PCT:-0.05}\" \"${STOPLOSS_COOLDOWN_CANDLES:-50}\"); uv run python scripts/run_live_trading.py scripts/strategies/macd_hist_immediate_entry_takeprofit_strategy.py --streams \"${STREAMS:-$STREAMS_JSON}\" --strategy-params \"${STRATEGY_PARAMS:-}\" --indicator-config \"${INDICATOR_CONFIG:-}\" --log-interval ${LOG_INTERVAL:-0} --yes"]
