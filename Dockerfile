FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir uv
RUN uv sync

# 테스트넷 라이브 트레이딩 실행 (필요하면 전략/옵션 변경)
CMD ["uv", "run", "python", "scripts/run_live_trading.py", "rsi_ultra_quick_test_strategy.py", "--symbol", "BTCUSDT", "--leverage", "5", "--interval", "1.0", "--candle-interval", "1m", "--max-position", "1.0", "--daily-loss-limit", "500.0", "--max-consecutive-losses", "0", "--yes"]
