"""전략 생성 파이프라인.

3단계 파이프라인을 통합하여 자연어 입력으로부터 검증된 전략 코드를 생성합니다.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm.code_generator import CodeGenerator
from llm.intent_parser import IntentParser, IntentResult, IntentType
from llm.spec_generator import SpecGenerator, StrategySpec
from llm.validator import ValidationResult, validate_all


@dataclass
class GenerationResult:
    """전략 생성 결과."""

    success: bool
    code: str | None = None
    validation_result: ValidationResult | None = None
    intent_result: IntentResult | None = None
    spec: StrategySpec | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    user_message: str | None = None


class StrategyGenerationPipeline:
    """전략 생성 파이프라인."""

    def __init__(
        self,
        llm_client=None,
        sample_data_path: Path | None = None,
    ) -> None:
        """파이프라인 초기화.

        Args:
            llm_client: LLM 클라이언트 (기본값: 새 인스턴스)
            sample_data_path: 샘플 데이터 경로 (기본값: data/sample_btc_1m.csv)
        """
        if llm_client is None:
            from llm.client import LLMClient

            llm_client = LLMClient()

        self.intent_parser = IntentParser(llm_client=llm_client)
        self.spec_generator = SpecGenerator()
        self.code_generator = CodeGenerator(llm_client=llm_client)
        self.sample_data_path = sample_data_path

    async def generate(
        self, 
        user_prompt: str, 
        manual_config: dict[str, Any] | None = None
    ) -> GenerationResult:
        """자연어 입력으로부터 전략 코드 생성.

        Args:
            user_prompt: 사용자의 자연어 입력
            manual_config: UI에서 입력한 정형 데이터 설정값

        Returns:
            GenerationResult
        """
        result = GenerationResult(success=False)

        # Stage 1: Intent Parser
        try:
            intent_result = await self.intent_parser.parse(user_prompt)
            result.intent_result = intent_result
            
            # user_message 처리
            if intent_result.user_message:
                result.user_message = intent_result.user_message

            # 의도 타입 확인
            if intent_result.intent_type == IntentType.OFF_TOPIC:
                result.errors.append("트레이딩 전략과 관련 없는 입력입니다.")
                return result

            if intent_result.intent_type == IntentType.INCOMPLETE:
                missing = ", ".join(intent_result.missing_elements) if intent_result.missing_elements else "정보"
                result.errors.append(f"입력이 불완전합니다. 누락된 요소: {missing}")
                result.warnings.append("기본값으로 진행할 수 있지만, 더 정확한 전략을 위해 누락된 정보를 제공해주세요.")
                # 경고만 하고 계속 진행

            if intent_result.intent_type == IntentType.CLARIFICATION_NEEDED:
                result.errors.append("추가 정보가 필요합니다.")
                if intent_result.missing_elements:
                    result.errors.extend([f"- {elem}" for elem in intent_result.missing_elements])
                return result

        except Exception as e:
            result.errors.append(f"의도 분석 실패: {str(e)}")
            return result

        # Stage 2: Spec Generator
        try:
            spec = self.spec_generator.generate(intent_result, manual_config)
            result.spec = spec

            # 기본값 경고
            if intent_result.missing_elements:
                result.warnings.append("일부 정보가 누락되어 기본값을 사용했습니다.")

        except Exception as e:
            result.errors.append(f"명세 생성 실패: {str(e)}")
            return result

        # Stage 3: Code Generator
        try:
            code = await self.code_generator.generate(spec)
            result.code = code

        except Exception as e:
            result.errors.append(f"코드 생성 실패: {str(e)}")
            return result

        # Stage 4: Validation
        try:
            validation_result = validate_all(code, self.sample_data_path)
            result.validation_result = validation_result

            if not validation_result.is_valid:
                result.errors.extend(validation_result.errors)
                result.warnings.extend(validation_result.warnings)
                result.success = False
                return result

            # 검증 경고 추가
            if validation_result.warnings:
                result.warnings.extend(validation_result.warnings)

        except Exception as e:
            result.errors.append(f"검증 실패: {str(e)}")
            return result

        # 성공
        result.success = True
        return result

    def get_clarification_questions(self, intent_result: IntentResult) -> list[str]:
        """확인 질문 생성.

        Args:
            intent_result: Intent 결과

        Returns:
            확인 질문 리스트
        """
        questions: list[str] = []

        if not intent_result.extracted_indicators:
            questions.append("어떤 지표를 사용하시겠습니까? (예: RSI, MACD, Bollinger Bands 등)")

        if not intent_result.entry_conditions:
            questions.append("언제 포지션을 진입하시겠습니까?")

        if not intent_result.exit_conditions:
            questions.append("언제 포지션을 청산하시겠습니까?")

        if intent_result.timeframe == "15m":  # 기본값인 경우
            questions.append("어떤 타임프레임을 사용하시겠습니까? (예: 1m, 15m, 4h 등)")

        return questions
