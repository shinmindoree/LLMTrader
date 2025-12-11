# LLMTrader

FastAPI 기반의 LLM·바이낸스 선물 자동 매매 백엔드 스캐폴드입니다.

## 요구사항
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (또는 표준 가상환경 + `pip`)

## 환경 설정

`.env` 파일 생성 후 아래 항목 설정:

```bash
# 바이낸스 선물 테스트넷 API (필수)
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
BINANCE_BASE_URL=https://testnet.binancefuture.com

# OpenAI API (LLM 전략 생성 시 필수)
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4
```

## 설치 및 실행

### 의존성 설치
```bash
uv sync --extra dev
```

### 웹 UI 실행 (권장)
```bash
uv run streamlit run streamlit_app.py
```

브라우저에서 `http://localhost:8501`로 접속하면 웹 UI가 열립니다.

### API 서버 실행 (선택)
```bash
uv run uvicorn llmtrader.main:app --reload
```

## 헬스체크
- `GET /healthz` → `{"status": "ok"}`
- `GET /status` → `{"env": "...", "binance_base_url": "..."}` (민감정보 없음)

## API 엔드포인트
- `POST /api/order` - 주문 생성
- `POST /api/order/cancel` - 주문 취소
- `POST /api/klines` - 캔들 데이터 조회

## 백테스트
```bash
# 샘플 전략(단순 이동평균 크로스) 백테스트 실행
uv run python scripts/run_backtest.py
```

## 페이퍼 트레이딩
```bash
# 실시간 시세로 가상 트레이딩 (Ctrl+C로 중지)
uv run python scripts/run_paper_trading.py
```

## LLM 전략 생성
```bash
# 자연어 설명으로 전략 코드 자동 생성
uv run python scripts/generate_strategy.py "5분봉에서 RSI가 30 이하면 매수, 70 이상이면 매도하는 전략" -o my_strategy.py

# 생성된 전략 백테스트
uv run python scripts/run_backtest.py  # (전략 파일 경로 수정 필요)
```

**필수**: `.env`에 `OPENAI_API_KEY` 설정 필요

## 테스트
```bash
uv run pytest
```

