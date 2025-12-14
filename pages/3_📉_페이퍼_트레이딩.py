"""페이퍼 트레이딩 페이지."""

import importlib.util
import sys
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="페이퍼 트레이딩", page_icon="📉", layout="wide")

st.title("📉 페이퍼 트레이딩")
st.markdown("실시간 시세로 가상 트레이딩을 실행합니다.")

st.divider()

st.info("""
💡 **페이퍼 트레이딩은 터미널에서 실행해주세요**

Streamlit UI에서는 장시간 실행되는 프로세스를 지원하지 않습니다.
아래 명령어로 터미널에서 실행하세요:

```bash
uv run python scripts/run_paper_trading_custom.py <전략파일> --symbol BTCUSDT
```
""")

st.divider()

# 전략 파일 선택
st.subheader("전략 선택")

strategy_files = list(Path(".").glob("*_strategy.py"))
strategy_files += list(Path("src/llmtrader/strategy/examples").glob("*.py"))

if not strategy_files:
    st.warning("전략 파일이 없습니다.")
    st.stop()

selected_file = st.selectbox(
    "전략 파일",
    options=strategy_files,
    format_func=lambda x: x.name,
)

# 설정
col1, col2 = st.columns(2)

with col1:
    symbol = st.text_input("심볼", value="BTCUSDT")
    balance = st.number_input("초기 자금 (USDT)", min_value=100.0, value=10000.0, step=100.0)

with col2:
    interval = st.number_input("가격 피드 간격 (초)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)

st.divider()

# 명령어 생성
command = f"uv run python scripts/run_paper_trading_custom.py {selected_file} --symbol {symbol} --balance {balance} --interval {interval}"

st.subheader("실행 명령어")
st.code(command, language="bash")

st.markdown("""
### 실행 방법

1. 위 명령어를 복사합니다
2. 터미널을 엽니다
3. 프로젝트 루트 디렉토리로 이동합니다
4. 명령어를 붙여넣고 실행합니다
5. `Ctrl+C`로 종료하면 요약 통계가 표시됩니다

### 출력 예시

```
[2025-12-10T08:30:00] Price: $92553.90 | Position: 0.0100 | Balance: $9074.56 | PnL: $12.34 | Total: $9086.90
[2025-12-10T08:30:01] Price: $92555.20 | Position: 0.0100 | Balance: $9074.56 | PnL: $13.64 | Total: $9088.20
...
```

종료 시:
```json
{
  "initial_balance": 10000.0,
  "final_equity": 10123.45,
  "total_return_pct": 1.23,
  "max_drawdown_pct": 0.56,
  "num_filled_orders": 8
}
```
""")

# 복사 버튼
if st.button("📋 명령어 복사", use_container_width=True):
    st.write("명령어가 클립보드에 복사되었습니다!")
    st.code(command, language="bash")


