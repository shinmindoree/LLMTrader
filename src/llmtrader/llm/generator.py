"""LLM 기반 전략 코드 생성."""

from openai import OpenAI

from llmtrader.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from llmtrader.settings import Settings


class StrategyGenerator:
    """전략 코드 생성기."""

    def __init__(self, settings: Settings) -> None:
        """생성기 초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai.api_key)

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




