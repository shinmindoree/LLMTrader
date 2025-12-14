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

## 주요 기능

### 1. 전략 생성 (LLM)
```bash
# 자연어 설명으로 전략 코드 자동 생성
uv run python scripts/generate_strategy.py "5분봉에서 RSI가 30 이하면 매수, 70 이상이면 매도하는 전략" -o my_strategy.py
```

**필수**: `.env`에 `OPENAI_API_KEY` 설정 필요

### 2. 백테스트
```bash
# 샘플 전략(단순 이동평균 크로스) 백테스트 실행
uv run python scripts/run_backtest.py

# 커스텀 전략 백테스트
uv run python scripts/run_backtest_custom.py my_strategy.py --symbol BTCUSDT --days 7
```

### 3. 페이퍼 트레이딩
```bash
# 실시간 시세로 가상 트레이딩 (Ctrl+C로 중지)
uv run python scripts/run_paper_trading.py

# 커스텀 전략으로 페이퍼 트레이딩
uv run python scripts/run_paper_trading_custom.py my_strategy.py --symbol BTCUSDT --balance 10000
```

### 4. 라이브 트레이딩 (NEW! ⚠️)
```bash
# 실제 테스트넷에서 자동 트레이딩
uv run python scripts/run_live_trading.py my_strategy.py \
  --symbol BTCUSDT \
  --leverage 1 \
  --max-position 0.5 \
  --daily-loss-limit 500
```

**⚠️ 경고**: 
- 반드시 **테스트넷 API**를 사용하세요 (`BINANCE_BASE_URL=https://testnet.binancefuture.com`)
- 전략을 충분히 백테스트/페이퍼 테스트한 후 사용하세요
- 리스크 관리 설정을 신중히 검토하세요

**리스크 관리 기능**:
- 레버리지 제한
- 최대 포지션 크기 제한
- 일일 손실 한도
- 연속 손실 보호
- 쿨다운 메커니즘
- 감사 로그 (모든 주문 기록)

## 테스트
```bash
uv run pytest
```

## 프로젝트 구조

```
LLMTrader/
├── src/llmtrader/
│   ├── api/           # FastAPI 라우터
│   ├── backtest/      # 백테스트 엔진
│   ├── binance/       # 바이낸스 API 클라이언트
│   ├── llm/           # LLM 전략 생성 파이프라인
│   ├── paper/         # 페이퍼 트레이딩 엔진
│   ├── live/          # 라이브 트레이딩 엔진 (NEW!)
│   │   ├── context.py # 라이브 트레이딩 컨텍스트
│   │   ├── engine.py  # 라이브 트레이딩 엔진
│   │   └── risk.py    # 리스크 관리 모듈
│   └── strategy/      # 전략 인터페이스
├── pages/             # Streamlit 페이지
│   ├── 1_🤖_전략_생성.py
│   ├── 2_📊_백테스트.py
│   ├── 3_📉_페이퍼_트레이딩.py
│   └── 4_🔴_라이브_트레이딩.py (NEW!)
├── scripts/           # 실행 스크립트
└── tests/             # 테스트
```

## 개발 로드맵

- [x] 프로젝트 스캐폴딩
- [x] LLM 전략 생성 파이프라인
- [x] 바이낸스 API 연동
- [x] 백테스트 엔진
- [x] 페이퍼 트레이딩 엔진
- [x] 라이브 트레이딩 엔진 (테스트넷)
- [x] 리스크 관리 시스템
- [ ] 운영/모니터링 강화
- [ ] 멀티 자산/거래소 지원

자세한 내용은 [development_plan.md](development_plan.md)를 참고하세요.

## 라이선스

MIT
