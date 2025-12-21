"""라이브 트레이딩 페이지."""

from pathlib import Path

import streamlit as st

st.set_page_config(page_title="라이브 트레이딩", page_icon="🔴", layout="wide")

st.title("🔴 라이브 트레이딩")
st.markdown("**실제 테스트넷 계좌에서 자동 트레이딩을 실행합니다.**")

st.divider()

# 경고 메시지
st.error("""
⚠️ **경고: 실제 주문이 실행됩니다!**

이 페이지는 바이낸스 테스트넷/메인넷에 실제 주문을 전송합니다.
- 반드시 **테스트넷 API**를 사용하세요.
- 먼저 `scripts/smoke_live_constraints.py` 같은 스모크 테스트로 "주문 체결"을 확인한 후 사용하세요.
- 리스크 관리 설정을 신중히 검토하세요.
""")

st.divider()

st.info("""
💡 **라이브 트레이딩은 터미널에서 실행해주세요**

Streamlit UI에서는 장시간 실행되는 프로세스를 지원하지 않습니다.
아래 명령어로 터미널에서 실행하세요:

```bash
uv run python scripts/run_live_trading.py <전략파일> --symbol BTCUSDT --leverage 1
```

Slack 알림(선택):
- 환경변수 `SLACK_WEBHOOK_URL` 를 설정하면 **포지션 진입/청산 시 Slack으로 알림**을 받을 수 있습니다.
""")

st.divider()

# 전략 파일 선택
st.subheader("1️⃣ 전략 선택")

strategy_files = list(Path(".").glob("*_strategy.py"))
strategy_files = [p for p in strategy_files if p.name != "generated_strategy.py"]

if not strategy_files:
    st.warning("전략 파일이 없습니다.")
    st.stop()

selected_file = st.selectbox(
    "전략 파일",
    options=strategy_files,
    format_func=lambda x: x.name,
)

# 설정
st.subheader("2️⃣ 거래 설정")

col1, col2 = st.columns(2)

with col1:
    symbol = st.text_input("심볼", value="BTCUSDT")
    leverage = st.number_input("레버리지", min_value=1, max_value=20, value=1, step=1)
    interval = st.number_input("가격 피드 간격 (초)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)

with col2:
    max_position = st.slider("최대 포지션 크기 (%)", min_value=10, max_value=100, value=50, step=10) / 100
    daily_loss_limit = st.number_input("일일 손실 한도 (USDT)", min_value=100.0, value=500.0, step=50.0)
    max_consecutive_losses = st.number_input(
        "최대 연속 손실 횟수 (0이면 비활성화)",
        min_value=0,
        max_value=10,
        value=0,
        step=1,
    )

st.divider()

# 리스크 관리 요약
st.subheader("3️⃣ 리스크 관리 요약")

risk_col1, risk_col2, risk_col3 = st.columns(3)

with risk_col1:
    st.metric("레버리지", f"{leverage}x")
    st.metric("최대 포지션", f"{max_position * 100:.0f}%")

with risk_col2:
    st.metric("일일 손실 한도", f"${daily_loss_limit:,.0f}")
    st.metric("연속 손실 제한", "비활성화" if max_consecutive_losses == 0 else f"{max_consecutive_losses}회")

with risk_col3:
    st.metric("쿨다운 시간", "300초 (5분)")
    st.metric("주문 크기 제한", "50% (자산 대비)")

st.divider()

# 명령어 생성
command = (
    f"uv run python scripts/run_live_trading.py {selected_file} "
    f"--symbol {symbol} "
    f"--leverage {leverage} "
    f"--interval {interval} "
    f"--max-position {max_position} "
    f"--daily-loss-limit {daily_loss_limit} "
    f"--max-consecutive-losses {max_consecutive_losses}"
)

st.subheader("4️⃣ 실행 명령어")
st.code(command, language="bash")

st.markdown("""
### 실행 방법

1. 위 명령어를 복사합니다
2. 터미널을 엽니다
3. 프로젝트 루트 디렉토리로 이동합니다
4. **반드시 .env 파일에서 테스트넷 API 설정을 확인합니다**
5. 명령어를 붙여넣고 실행합니다
6. "yes"를 입력하여 확인합니다
7. `Ctrl+C`로 종료하면 요약 통계와 감사 로그가 저장됩니다

### 출력 예시

```
[2025-12-11T08:30:00] Price: $92553.90 | Position: +0.0100 | Balance: $9074.56 | PnL: +12.34 | Total: $9086.90
[2025-12-11T08:30:01] Price: $92555.20 | Position: +0.0100 | Balance: $9074.56 | PnL: +13.64 | Total: $9088.20
...
```

종료 시:
```json
{
  "initial_equity": 10000.0,
  "final_equity": 10123.45,
  "total_return_pct": 1.23,
  "max_drawdown_pct": 0.56,
  "num_filled_orders": 8,
  "risk_status": {
    "daily_pnl": 123.45,
    "consecutive_losses": 0,
    "is_in_cooldown": false
  }
}
```

### 감사 로그

모든 주문과 이벤트가 `audit_log_*.json` 파일에 기록됩니다:
- 주문 실행/취소
- 리스크 관리 차단
- 오류 발생
- 계좌 상태 업데이트
""")

# 복사 버튼
if st.button("📋 명령어 복사", use_container_width=True):
    st.write("명령어가 클립보드에 복사되었습니다!")
    st.code(command, language="bash")

st.divider()

# 추가 안내
st.subheader("📚 추가 정보")

with st.expander("리스크 관리 상세"):
    st.markdown("""
    ### 자동 리스크 관리 기능
    
    1. **포지션 크기 제한**
       - 단일 주문: 총 자산의 50%까지
       - 전체 포지션: 설정한 최대 포지션까지
    
    2. **일일 손실 한도**
       - 설정한 금액 이상 손실 시 당일 거래 중지
       - 매일 자정(UTC)에 리셋
    
    3. **연속 손실 보호**
       - 설정한 횟수만큼 연속 손실 시 거래 중지
       - 수익 거래 발생 시 카운터 리셋
    
    4. **쿨다운 메커니즘**
       - 손실 거래 후 5분간 새 거래 금지
       - 감정적 거래 방지
    
    5. **레버리지 제한**
       - 설정한 레버리지 이하로만 거래
       - 과도한 위험 노출 방지
    """)

with st.expander("감사 로그 예시"):
    st.code("""
[
  {
    "timestamp": "2025-12-11T08:30:00",
    "action": "LEVERAGE_SET",
    "data": {"leverage": 1}
  },
  {
    "timestamp": "2025-12-11T08:30:05",
    "action": "ORDER_PLACED",
    "data": {
      "order_id": 12345,
      "side": "BUY",
      "quantity": 0.01,
      "type": "MARKET"
    }
  },
  {
    "timestamp": "2025-12-11T08:35:10",
    "action": "ORDER_REJECTED_RISK",
    "data": {
      "side": "SELL",
      "quantity": 0.02,
      "reason": "쿨다운 중 (남은 시간: 120초)"
    }
  }
]
    """, language="json")

with st.expander("자주 묻는 질문"):
    st.markdown("""
    ### Q: 테스트넷과 메인넷의 차이는?
    A: `.env` 파일의 `BINANCE_BASE_URL`로 구분합니다.
    - 테스트넷: `https://testnet.binancefuture.com` (가상 자금)
    - 메인넷: `https://fapi.binance.com` (실제 자금)
    
    ### Q: 중간에 멈추면 포지션은?
    A: 포지션은 유지됩니다. 다시 시작하면 기존 포지션을 인식합니다.
    
    ### Q: 여러 전략을 동시에 실행할 수 있나요?
    A: 가능하지만, 같은 심볼을 사용하면 포지션이 겹칩니다.
    
    ### Q: 리스크 한도에 걸리면?
    A: 자동으로 거래가 중지되고 로그에 기록됩니다.
    """)

