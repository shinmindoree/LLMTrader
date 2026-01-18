# LLMTrader

바이낸스 선물 **백테스트 + 라이브 트레이딩(테스트넷/메인넷)** 실행을 위한 프로젝트입니다.

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

### (선택) TA-Lib 기반 builtin 인디케이터 사용

이 프로젝트의 builtin 인디케이터는 TA-Lib 함수명을 통해 호출됩니다(예: `RSI`, `EMA`, `MACD`).

1) TA-Lib 라이브러리 설치 (macOS 예시)
```bash
brew install ta-lib
```

2) Python 패키지 설치
```bash
uv sync --extra talib
```

### 웹 UI 실행 (권장)
```bash
uv run streamlit run streamlit_app.py
```

브라우저에서 `http://localhost:8501`로 접속하면 웹 UI가 열립니다.

### API 서버
현재 저장소에는 FastAPI 기반 API 서버가 포함되어 있지 않습니다(추후 추가 예정).

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

전략 파일을 새로 만들 때는 `indicator_strategy_template.py`를 복사해서 시작하면 됩니다.

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
- 아직 `pytest` 기반 테스트 스위트는 구성되어 있지 않습니다.
- 대신 `scripts/smoke_live_constraints.py`, `scripts/min_order_test.py`, `scripts/check_time_sync.py`로 스모크/헬스체크를 수행할 수 있습니다.

## 프로젝트 구조

```
LLMTrader/
├── src/
│   ├── binance/       # 바이낸스 REST/WS 클라이언트
│   ├── live/          # 라이브 트레이딩 엔진 (LiveContext/Engine/PriceFeed/Risk)
│   ├── backtest/      # 백테스트 엔진 (Context/Engine/DataFetcher)
│   ├── strategy/      # 전략 인터페이스 (Strategy / StrategyContext)
│   ├── indicators/    # 지표 계산(RSI/SMA/EMA 등)
│   ├── common/        # 공통 모듈(리스크 설정/검증 등)
│   ├── notifications/ # Slack 알림 등
│   └── settings.py    # 환경변수(.env) 설정 로더
├── pages/             # Streamlit 페이지
│   └── 4_🔴_라이브_트레이딩.py
├── scripts/           # 실행 스크립트
└── *.py               # 샘플 전략 파일 등
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
