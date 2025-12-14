"""전략 생성 페이지."""

import asyncio
from pathlib import Path

import streamlit as st

from llmtrader.llm.pipeline import StrategyPipeline
from llmtrader.settings import get_settings

st.set_page_config(page_title="전략 생성", page_icon="🤖", layout="wide")

st.title("🤖 전략 생성")
st.markdown("자연어로 트레이딩 전략을 설명하면 LLM이 Python 코드를 생성합니다.")

st.divider()

# 설정 확인
settings = get_settings()

if not settings.openai.api_key:
    st.error("⚠️ OPENAI_API_KEY가 설정되지 않았습니다. `.env` 파일에 추가해주세요.")
    st.stop()

# 전략 설명 입력
st.subheader("1️⃣ 전략 설명")

description = st.text_area(
    "전략을 자연어로 설명해주세요",
    placeholder="예: 10일 이동평균과 30일 이동평균이 교차하면 매수하고, 반대로 교차하면 매도",
    height=100,
)

# 설정
col1, col2 = st.columns(2)

with col1:
    output_filename = st.text_input(
        "저장 파일 이름",
        value="generated_strategy.py",
        help="생성된 전략을 저장할 파일 이름",
    )

with col2:
    max_retries = st.number_input(
        "최대 재시도 횟수",
        min_value=1,
        max_value=5,
        value=3,
        help="생성 실패 시 재시도 횟수",
    )

st.divider()

# 생성 버튼
if st.button("🚀 전략 생성", type="primary", use_container_width=True):
    if not description.strip():
        st.error("전략 설명을 입력해주세요.")
    else:
        with st.spinner("전략을 생성중입니다..."):
            # 진행 상황 표시
            progress_placeholder = st.empty()
            log_placeholder = st.empty()

            async def generate():
                pipeline = StrategyPipeline(settings, max_retries=max_retries)

                success, code, metadata = await pipeline.generate_and_validate(description)

                return success, code, metadata

            # 비동기 실행
            success, code, metadata = asyncio.run(generate())

            st.divider()

            if success:
                st.success(f"✅ 전략이 성공적으로 생성되었습니다! (시도: {metadata['attempts']}회)")

                # 코드 표시
                st.subheader("생성된 코드")
                st.code(code, language="python", line_numbers=True)

                # 파일 저장
                output_path = Path(output_filename)
                output_path.write_text(code, encoding="utf-8")
                st.info(f"📁 파일 저장: `{output_path}`")

                # 다운로드 버튼
                st.download_button(
                    label="💾 다운로드",
                    data=code,
                    file_name=output_filename,
                    mime="text/x-python",
                )

                # 메타데이터
                with st.expander("📋 생성 메타데이터"):
                    st.json(metadata)

                # 다음 단계 안내
                st.divider()
                st.info("💡 다음 단계: 백테스트 페이지에서 생성된 전략을 테스트해보세요!")

            else:
                # 입력 검증 실패인지 확인
                input_validation = metadata.get("input_validation", {})
                if input_validation and not input_validation.get("is_valid", True):
                    st.error("❌ 트레이딩 전략 설명이 아닙니다!")
                    st.warning(f"**사유**: {input_validation.get('reason', '알 수 없음')}")
                    st.info("""
                    💡 **올바른 전략 설명 예시:**
                    - "RSI가 30 이하면 매수, 70 이상이면 매도"
                    - "이동평균선 크로스오버 전략"
                    - "볼린저 밴드 상단/하단에서 매매"
                    
                    트레이딩 로직(매수/매도 조건, 기술적 지표 등)을 포함해주세요.
                    """)
                else:
                    st.error(f"❌ 전략 생성 실패 ({metadata['attempts']}회 시도)")
                    st.code(code, language="text")

                with st.expander("🔍 오류 상세"):
                    st.json(metadata)

# 사용 예시
with st.expander("💡 전략 설명 예시"):
    st.markdown("""
    **이동평균 크로스오버**
    ```
    10일 이동평균과 30일 이동평균이 교차하면 매수하고, 반대로 교차하면 매도
    ```

    **RSI 전략**
    ```
    5분봉에서 RSI가 30 이하면 매수, 70 이상이면 매도하는 전략
    ```

    **볼린저 밴드**
    ```
    가격이 볼린저 밴드 하단을 터치하면 매수, 상단을 터치하면 매도
    ```

    **모멘텀 전략**
    ```
    최근 5개 캔들의 평균 가격보다 현재가가 5% 이상 높으면 매수, 5% 이상 낮으면 매도
    ```
    """)


