"""LLMTrader Streamlit UI 메인 앱."""

import streamlit as st

st.set_page_config(
    page_title="LLMTrader",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 메인 페이지
st.title("📈 LLMTrader")
st.markdown("### LLM 기반 바이낸스 선물 자동 트레이딩 시스템")

st.divider()

# 주요 기능 소개
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("🤖 전략 생성")
    st.markdown("""
    자연어로 트레이딩 전략을 설명하면
    LLM이 자동으로 Python 코드를 생성합니다.
    
    - OpenAI GPT-4 기반
    - 정적 분석 & 샌드박스 검증
    - 재프롬프트 루프로 오류 수정
    """)
    if st.button("전략 생성하기", key="nav_strategy", use_container_width=True):
        st.switch_page("pages/1_🤖_전략_생성.py")

with col2:
    st.subheader("📊 백테스트")
    st.markdown("""
    과거 데이터로 전략 성능을 검증합니다.
    
    - 히스토리컬 캔들 데이터
    - 수수료/슬리피지 반영
    - PnL, MDD, 샤프, 승률 리포트
    """)
    if st.button("백테스트 실행", key="nav_backtest", use_container_width=True):
        st.switch_page("pages/2_📊_백테스트.py")

with col3:
    st.subheader("📉 페이퍼 트레이딩")
    st.markdown("""
    실시간 시세로 가상 트레이딩을 실행합니다.
    
    - 실시간 시세 피드
    - 가상 체결 엔진
    - 포지션/PNL 추적
    """)
    if st.button("페이퍼 시작", key="nav_paper", use_container_width=True):
        st.switch_page("pages/3_📉_페이퍼_트레이딩.py")

st.divider()

# 시스템 상태
st.subheader("⚙️ 시스템 상태")

from llmtrader.settings import get_settings

settings = get_settings()

status_col1, status_col2, status_col3 = st.columns(3)

with status_col1:
    st.metric("환경", settings.env.upper())

with status_col2:
    binance_status = "✅ 설정됨" if settings.binance.api_key else "❌ 미설정"
    st.metric("Binance API", binance_status)

with status_col3:
    openai_status = "✅ 설정됨" if settings.openai.api_key else "❌ 미설정"
    st.metric("OpenAI API", openai_status)

st.info(f"**Binance URL**: {settings.binance.base_url}")

# 푸터
st.divider()
st.caption("LLMTrader v0.1.0 | 바이낸스 선물 테스트넷 전용")

