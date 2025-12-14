"""LLM 기반 전략 코드 생성."""

import json

from openai import OpenAI

from llmtrader.llm.prompts import (
    SYSTEM_PROMPT,
    VALIDATION_SYSTEM_PROMPT,
    VALIDATION_USER_PROMPT,
    build_user_prompt,
)
from llmtrader.settings import Settings


class InvalidStrategyDescriptionError(Exception):
    """유효하지 않은 전략 설명 오류."""

    def __init__(self, reason: str) -> None:
        """오류 초기화.

        Args:
            reason: 거부 사유
        """
        self.reason = reason
        super().__init__(f"유효하지 않은 전략 설명: {reason}")


class StrategyGenerator:
    """전략 코드 생성기."""

    def __init__(self, settings: Settings) -> None:
        """생성기 초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai.api_key)

    async def validate_description(self, description: str) -> tuple[bool, str]:
        """입력이 유효한 트레이딩 전략 설명인지 검증.

        Args:
            description: 사용자 입력

        Returns:
            (유효 여부, 사유)
        """
        # 빈 입력 체크
        if not description or not description.strip():
            return False, "입력이 비어있습니다."

        # 너무 짧은 입력 체크
        if len(description.strip()) < 5:
            return False, "입력이 너무 짧습니다. 전략을 더 자세히 설명해주세요."

        # LLM으로 검증
        try:
            response = self.client.chat.completions.create(
                model=self.settings.openai.model,
                messages=[
                    {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
                    {"role": "user", "content": VALIDATION_USER_PROMPT.format(description=description)},
                ],
                temperature=0.0,  # 일관된 결과를 위해 낮은 temperature
                max_tokens=200,
            )

            if not response.choices:
                return False, "검증 응답을 받지 못했습니다."

            content = response.choices[0].message.content
            if not content:
                return False, "검증 응답이 비어있습니다."

            # JSON 파싱
            try:
                result = json.loads(content)
                is_valid = result.get("is_valid", False)
                reason = result.get("reason", "알 수 없는 오류")
                return is_valid, reason
            except json.JSONDecodeError:
                # JSON 파싱 실패 시 텍스트에서 판단
                if "true" in content.lower():
                    return True, "유효한 전략 설명입니다."
                return False, "응답을 파싱할 수 없습니다."

        except Exception as e:  # noqa: BLE001
            # API 오류 시 일단 통과 (실제 생성에서 실패하면 그때 처리)
            print(f"검증 중 오류 발생: {e}")
            return True, "검증을 건너뛰었습니다."

    async def generate(self, description: str) -> str:
        """전략 설명으로부터 Python 코드 생성.

        Args:
            description: 자연어 전략 설명

        Returns:
            생성된 Python 코드

        Raises:
            Exception: API 호출 실패 시
        """
        user_prompt = build_user_prompt(description)

        response = self.client.chat.completions.create(
            model=self.settings.openai.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.settings.openai.temperature,
            max_tokens=self.settings.openai.max_tokens,
        )

        if not response.choices:
            raise ValueError("No response from OpenAI")

        code = response.choices[0].message.content
        if not code:
            raise ValueError("Empty code from OpenAI")

        # 마크다운 코드 블록 제거 (```python ... ``` 형식)
        code = self._strip_markdown(code)

        return code

    def _strip_markdown(self, code: str) -> str:
        """마크다운 코드 블록 제거.

        Args:
            code: 원본 코드

        Returns:
            정제된 코드
        """
        lines = code.strip().split("\n")

        # 첫 줄이 ```python 또는 ``` 이면 제거
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        # 마지막 줄이 ``` 이면 제거
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        return "\n".join(lines).strip()




