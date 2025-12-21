# LLMTrader

바이낸스 선물 **라이브 트레이딩(테스트넷/메인넷)** 실행을 위한 프로젝트입니다.

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

# Slack 알림(선택)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
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

### 라이브 트레이딩 (⚠️)
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
- 먼저 `scripts/smoke_live_constraints.py` 스모크 테스트로 **주문 체결**을 확인하세요
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
│   ├── binance/       # 바이낸스 API 클라이언트
│   ├── live/          # 라이브 트레이딩 엔진
│   │   ├── context.py # 라이브 트레이딩 컨텍스트
│   │   ├── engine.py  # 라이브 트레이딩 엔진
│   │   ├── price_feed.py # 가격 피드(REST 폴링)
│   │   └── risk.py    # 리스크 관리 모듈
│   └── strategy/      # 전략 인터페이스
├── pages/             # Streamlit 페이지
│   └── 4_🔴_라이브_트레이딩.py
├── scripts/           # 실행 스크립트
└── tests/             # 테스트
```

## 개발 로드맵

- [x] 프로젝트 스캐폴딩
- [x] 바이낸스 API 연동
- [x] 라이브 트레이딩 엔진 (테스트넷/메인넷)
- [x] 리스크 관리 시스템
- [ ] 운영/모니터링 강화
- [ ] 멀티 자산/거래소 지원

자세한 내용은 [development_plan.md](development_plan.md)를 참고하세요.

## 라이선스

MIT
