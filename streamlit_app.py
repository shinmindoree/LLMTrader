"""AlphaWeaver Streamlit UI (라이브 트레이딩 전용)."""

import sys
from pathlib import Path

import streamlit as st

# src 디렉토리를 Python 경로에 추가
project_root = Path(__file__).parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

st.set_page_config(
    page_title="AlphaWeaver",
    page_icon="🧵",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 메인 페이지
st.title("🧵 AlphaWeaver")
st.markdown("### 바이낸스 선물 자동 트레이딩 (라이브 전용)")

st.divider()

st.subheader("📊 백테스트")
st.markdown("""
과거 데이터를 사용하여 전략을 테스트합니다.

- 과거 데이터 기반 시뮬레이션
- 실제 주문 없이 안전하게 테스트
- 수수료 및 레버리지 반영
- 상세한 결과 분석
""")
if st.button("백테스트 실행", key="nav_backtest", use_container_width=True):
    st.switch_page("pages/3_📊_백테스트.py")

st.divider()

st.subheader("🔴 라이브 트레이딩")
st.markdown("""
실제 테스트넷(또는 메인넷)에서 자동 트레이딩을 실행합니다.

- 실제 주문 실행
- 리스크 관리 (레버리지/최대 포지션/손실 한도)
- Slack 알림 & 감사 로그
""")
if st.button("라이브 트레이딩 설정/가이드", key="nav_live", use_container_width=True):
    st.switch_page("pages/4_🔴_라이브_트레이딩.py")

st.divider()

# 시스템 상태
st.subheader("⚙️ 시스템 상태")

from settings import get_settings

settings = get_settings()

status_col1, status_col2, status_col3 = st.columns(3)

with status_col1:
    st.metric("환경", settings.env.upper())

with status_col2:
    binance_status = "✅ 설정됨" if settings.binance.api_key else "❌ 미설정"
    st.metric("Binance API", binance_status)

with status_col3:
    slack_status = "✅ 설정됨" if settings.slack.webhook_url else "➖ 미설정"
    st.metric("Slack 알림", slack_status)

st.info(f"**Binance URL**: {settings.binance.base_url}")

# 푸터
st.divider()
st.caption("AlphaWeaver v0.1.0 | 바이낸스 선물 테스트넷 전용")


