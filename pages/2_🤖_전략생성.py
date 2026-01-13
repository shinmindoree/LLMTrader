"""전략 생성 페이지.

자연어로 트레이딩 전략을 생성하고 검증하여 저장할 수 있는 인터페이스.
"""

import asyncio
import sys
from pathlib import Path

import streamlit as st

# src 디렉토리를 Python 경로에 추가
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from llm.intent_parser import IntentType
from llm.pipeline import StrategyGenerationPipeline
from llm.validator import validate_all

# 페이지 설정
st.set_page_config(
    page_title="전략 생성 - LLMTrader",
    page_icon="🤖",
    layout="wide",
)

# 코드 블록 너비 반응형 스타일
st.markdown("""
<style>
    div[data-testid="stCodeBlock"] {
        width: 100% !important;
        max-width: 100% !important;
    }
    div[data-testid="stCodeBlock"] pre {
        width: 100% !important;
        max-width: 100% !important;
        overflow-x: auto !important;
    }
    .stCode {
        width: 100% !important;
        max-width: 100% !important;
    }
    .element-container:has(div[data-testid="stCodeBlock"]) {
        width: 100% !important;
        max-width: 100% !important;
    }
    pre {
        width: 100% !important;
        max-width: 100% !important;
        overflow-x: auto !important;
    }
    code {
        white-space: pre !important;
    }
</style>
""", unsafe_allow_html=True)

# 세션 상태 초기화
st.session_state.setdefault("generated_code", None)
st.session_state.setdefault("validation_result", None)
st.session_state.setdefault("intent_result", None)
st.session_state.setdefault("spec", None)
st.session_state.setdefault("generation_result", None)
st.session_state.setdefault("strategy_name", "GeneratedStrategy")
st.session_state.setdefault("show_code", False)

# 제목 및 설명
st.title("🤖 전략 생성")
st.markdown("""
자연어로 트레이딩 전략을 생성합니다. 생성된 전략은 자동으로 검증되며, 
수정 후 저장하여 백테스트 및 라이브 트레이딩에서 사용할 수 있습니다.
""")

st.divider()

# 자연어 입력 영역
st.subheader("📝 자연어 입력")
user_input = st.text_area(
    "트레이딩 전략을 자연어로 설명해주세요.",
    height=150,
    placeholder="예: RSI가 30 아래에서 30을 상향 돌파하면 롱 진입, RSI가 70을 넘으면 청산",
    key="user_input",
)

col1, col2 = st.columns([1, 4])
with col1:
    generate_button = st.button("생성하기", type="primary", use_container_width=True)

# 생성 버튼 클릭 시 처리
if generate_button:
    if not user_input or not user_input.strip():
        st.error("전략 설명을 입력해주세요.")
    else:
        with st.spinner("전략을 생성하는 중..."):
            try:
                # 샘플 데이터 경로 설정
                sample_data_path = project_root / "data" / "sample_btc_1m.csv"
                if not sample_data_path.exists():
                    sample_data_path = None

                # 파이프라인 생성 및 실행
                pipeline = StrategyGenerationPipeline(sample_data_path=sample_data_path)
                result = asyncio.run(pipeline.generate(user_input))

                # 세션 상태에 저장
                st.session_state.generation_result = result
                st.session_state.intent_result = result.intent_result
                st.session_state.spec = result.spec
                st.session_state.generated_code = result.code
                st.session_state.validation_result = result.validation_result

                # 전략 이름 추출 (Intent에서)
                if result.intent_result and result.intent_result.extracted_indicators:
                    indicators_str = "_".join(result.intent_result.extracted_indicators)
                    st.session_state.strategy_name = f"{indicators_str.capitalize()}Strategy"
                else:
                    st.session_state.strategy_name = "GeneratedStrategy"

                st.rerun()

            except Exception as e:
                st.error(f"전략 생성 중 오류가 발생했습니다: {str(e)}")
                st.exception(e)

# 생성 결과 표시
if st.session_state.generation_result:
    result = st.session_state.generation_result

    # Intent 결과 처리
    if result.intent_result:
        intent_result = result.intent_result

        # Off-topic 처리
        if intent_result.intent_type == IntentType.OFF_TOPIC:
            st.error("❌ 트레이딩 전략과 관련 없는 입력입니다.")
            with st.expander("💡 전략 예시 보기"):
                st.markdown("""
                **예시 전략:**
                - RSI가 30 아래에서 30을 상향 돌파하면 롱 진입, RSI가 70을 넘으면 청산
                - MACD가 시그널선을 상향 돌파하면 롱 진입, 하향 돌파하면 청산
                - 볼린저 밴드 하단 터치 시 롱 진입, 상단 터치 시 청산
                - RSI가 30에서 롱 진입, 70에서 청산하고, RSI가 70에서 숏 진입, 30에서 청산
                """)
            st.stop()

        # Incomplete 처리
        if intent_result.intent_type == IntentType.INCOMPLETE:
            st.warning("⚠️ 입력이 불완전합니다.")
            if intent_result.missing_elements:
                st.info(f"**누락된 요소:** {', '.join(intent_result.missing_elements)}")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("기본값으로 진행", use_container_width=True):
                    # 기본값으로 진행 (이미 파이프라인에서 처리됨)
                    st.info("기본값을 사용하여 전략을 생성합니다.")
            with col2:
                if st.button("취소", use_container_width=True):
                    st.session_state.generation_result = None
                    st.session_state.generated_code = None
                    st.rerun()

        # Clarification needed 처리
        if intent_result.intent_type == IntentType.CLARIFICATION_NEEDED:
            st.error("❌ 추가 정보가 필요합니다.")
            if intent_result.missing_elements:
                st.markdown("**필요한 정보:**")
                for elem in intent_result.missing_elements:
                    st.markdown(f"- {elem}")
            st.stop()

    # 에러 표시
    if result.errors:
        st.error("❌ 전략 생성 중 오류가 발생했습니다:")
        for error in result.errors:
            st.markdown(f"- {error}")

    # 경고 표시
    if result.warnings:
        st.warning("⚠️ 경고:")
        for warning in result.warnings:
            st.markdown(f"- {warning}")

    # 성공 시 코드 표시
    if result.success and result.code:
        st.divider()
        st.subheader("📄 생성된 코드")

        # 코드 수정 가능 영역
        edited_code = st.text_area(
            "생성된 코드를 확인하고 수정할 수 있습니다.",
            value=st.session_state.generated_code or result.code,
            height=600,
            key="code_editor",
        )

        # 코드 복사 버튼
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("📋 코드 표시", use_container_width=True):
                st.session_state.show_code = not st.session_state.get("show_code", False)
        
        # 코드 블록은 컬럼 밖에서 전체 너비로 표시
        if st.session_state.get("show_code", False):
            st.code(edited_code, language="python")
            st.info("위 코드 블록을 선택하여 복사하세요")

        with col2:
            if st.button("🔄 재검증", use_container_width=True):
                        # 수정된 코드로 재검증
                st.session_state.generated_code = edited_code
                with st.spinner("코드를 검증하는 중..."):
                    try:
                        sample_data_path = project_root / "data" / "sample_btc_1m.csv"
                        if not sample_data_path.exists():
                            sample_data_path = None
                        validation_result = validate_all(edited_code, sample_data_path)
                        st.session_state.validation_result = validation_result
                        st.rerun()
                    except Exception as e:
                        st.error(f"검증 중 오류가 발생했습니다: {str(e)}")

        # 검증 결과 표시
        st.divider()
        st.subheader("✅ 검증 결과")

        validation_result = st.session_state.validation_result or result.validation_result

        if validation_result:
            if validation_result.is_valid:
                st.success("✅ 검증 통과! 전략을 저장할 수 있습니다.")
                
                if validation_result.warnings:
                    st.warning("⚠️ 경고:")
                    for warning in validation_result.warnings:
                        st.markdown(f"- {warning}")
            else:
                st.error("❌ 검증 실패:")
                
                # Level별 에러 구분
                if validation_result.level == "static":
                    st.markdown("**Level 1: 정적 검증 실패** (문법, 금지된 import)")
                elif validation_result.level == "structure":
                    st.markdown("**Level 2: 구조 검증 실패** (Strategy 상속, 필수 메서드)")
                elif validation_result.level == "runtime":
                    st.markdown("**Level 3: 런타임 검증 실패** (실제 실행)")
                
                for error in validation_result.errors:
                    st.markdown(f"- {error}")

                # 수정 가이드
                with st.expander("💡 일반적인 에러 해결 방법"):
                    st.markdown("""
                    **문법 오류:**
                    - 괄호, 따옴표가 제대로 닫혔는지 확인
                    - 들여쓰기가 올바른지 확인
                    
                    **구조 오류:**
                    - `from strategy.base import Strategy` import 확인
                    - `initialize(ctx)` 메서드가 있는지 확인
                    - `on_bar(ctx, bar)` 메서드가 있는지 확인
                    - 클래스가 `Strategy`를 상속하는지 확인
                    
                    **런타임 오류:**
                    - `ctx.get_indicator()` 사용법 확인
                    - 지표명이 올바른지 확인 (rsi, macd, bollinger 등)
                    - 포지션 관리 로직 확인
                    """)

        # 저장 기능
        st.divider()
        st.subheader("💾 전략 저장")

        strategy_name = st.text_input(
            "전략 이름",
            value=st.session_state.strategy_name,
            key="strategy_name_input",
            help="파일명은 '{전략이름}_strategy.py' 형식으로 저장됩니다.",
        )

        # 파일명 생성
        if strategy_name:
            # 공백과 특수문자 제거
            safe_name = "".join(c for c in strategy_name if c.isalnum() or c in ("_", "-"))
            if not safe_name.endswith("Strategy"):
                safe_name = f"{safe_name}Strategy"
            filename = f"{safe_name}_strategy.py"
            filepath = project_root / filename

            # 저장 버튼
            can_save = (
                validation_result
                and validation_result.is_valid
                and edited_code
                and strategy_name
            )

            if can_save:
                if st.button("💾 저장", type="primary", use_container_width=True):
                    try:
                        # 파일 저장
                        filepath.write_text(edited_code, encoding="utf-8")
                        st.success(f"✅ 전략이 저장되었습니다: `{filename}`")
                        st.info(f"저장 위치: `{filepath}`")
                        
                        # 세션 상태 초기화
                        st.session_state.generated_code = None
                        st.session_state.validation_result = None
                        st.session_state.intent_result = None
                        st.session_state.spec = None
                        st.session_state.generation_result = None
                        
                        # 페이지 새로고침
                        st.rerun()
                    except Exception as e:
                        st.error(f"파일 저장 중 오류가 발생했습니다: {str(e)}")
            else:
                if not validation_result or not validation_result.is_valid:
                    st.info("검증을 통과해야 저장할 수 있습니다.")
                elif not edited_code:
                    st.info("코드를 입력해주세요.")
                elif not strategy_name:
                    st.info("전략 이름을 입력해주세요.")

        # 생성된 전략 목록 표시
        st.divider()
        st.subheader("📋 생성된 전략 목록")

        strategy_files = sorted(
            [
                f
                for f in project_root.glob("*_strategy.py")
                if f.is_file() and f.name != "__init__.py"
            ],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        if strategy_files:
            for strategy_file in strategy_files:
                file_name = strategy_file.name
                st.markdown(f"- {file_name}")
        else:
            st.info("아직 생성된 전략이 없습니다.")

# 사이드바에 도움말
with st.sidebar:
    st.header("💡 도움말")
    st.markdown("""
    **전략 생성 가이드:**
    
    1. 자연어로 전략을 설명하세요
    2. 생성된 코드를 확인하고 수정하세요
    3. 검증을 통과하면 저장하세요
    
    **예시 입력:**
    - "RSI가 30에서 롱 진입, 70에서 청산"
    - "MACD 크로스오버 전략"
    - "볼린저 밴드 하단 터치 시 매수"
    """)
