"""전략 코드 생성 파이프라인."""

import tempfile
from pathlib import Path
from typing import Any

from llmtrader.llm.generator import InvalidStrategyDescriptionError, StrategyGenerator
from llmtrader.llm.sandbox import SandboxRunner
from llmtrader.llm.validator import CodeValidator
from llmtrader.settings import Settings


class StrategyPipeline:
    """전략 코드 생성 및 검증 파이프라인."""

    def __init__(self, settings: Settings, max_retries: int = 3) -> None:
        """파이프라인 초기화.

        Args:
            settings: 애플리케이션 설정
            max_retries: 최대 재시도 횟수
        """
        self.generator = StrategyGenerator(settings)
        self.validator = CodeValidator()
        self.sandbox = SandboxRunner()
        self.max_retries = max_retries

    async def generate_and_validate(
        self,
        description: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """전략 생성 및 검증 실행.

        Args:
            description: 전략 설명

        Returns:
            (성공 여부, 최종 코드 또는 오류 메시지, 메타데이터)
        """
        metadata: dict[str, Any] = {
            "attempts": 0,
            "validation_errors": [],
            "lint_warnings": [],
            "input_validation": None,
        }

        # 0. 입력 검증 (트레이딩 전략 설명인지 확인)
        print("\n=== Input Validation ===")
        is_valid_input, validation_reason = await self.generator.validate_description(description)
        metadata["input_validation"] = {
            "is_valid": is_valid_input,
            "reason": validation_reason,
        }

        if not is_valid_input:
            error_msg = f"❌ 유효하지 않은 입력: {validation_reason}"
            print(error_msg)
            return False, error_msg, metadata

        print(f"✅ 입력 검증 통과: {validation_reason}")

        previous_errors: list[str] = []

        for attempt in range(self.max_retries):
            metadata["attempts"] = attempt + 1
            print(f"\n=== Attempt {attempt + 1}/{self.max_retries} ===")

            # 1. 코드 생성 (이전 오류 피드백 포함)
            try:
                print("Generating code...")
                if previous_errors:
                    # 재시도 시 이전 오류를 프롬프트에 포함
                    error_feedback = "\n".join(previous_errors[-2:])  # 최근 2개 오류만
                    enhanced_description = (
                        f"{description}\n\n"
                        f"Previous attempt had these errors:\n{error_feedback}\n"
                        f"Please fix these issues in the generated code."
                    )
                    code = await self.generator.generate(enhanced_description)
                else:
                    code = await self.generator.generate(description)
                print(f"Generated {len(code)} characters")
            except Exception as e:  # noqa: BLE001
                error_msg = f"Generation failed: {e}"
                print(error_msg)
                metadata["validation_errors"].append(error_msg)
                previous_errors.append(error_msg)
                continue

            # 2. 정적 검증 (구문, import, 위험 함수)
            print("Validating code...")
            is_valid, errors = self.validator.validate(code)
            if not is_valid:
                print(f"Validation errors: {errors}")
                metadata["validation_errors"].extend(errors)
                # 오류를 다음 시도에 피드백
                previous_errors.extend(errors)
                continue

            # 3. 포맷팅 (black)
            print("Formatting code...")
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)

            success, formatted_code = self.validator.format_code(code, tmp_path)
            if success:
                code = formatted_code
                print("Code formatted")
            else:
                print(f"Formatting warning: {formatted_code}")

            # 4. 린트 (ruff)
            print("Linting code...")
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)

            lint_ok, lint_messages = self.validator.lint_code(code, tmp_path)
            if lint_messages:
                print(f"Lint messages: {lint_messages}")
                metadata["lint_warnings"].extend(lint_messages)

            # 5. 샌드박스 스모크 테스트
            print("Running smoke test...")
            smoke_ok, smoke_msg = self.sandbox.smoke_test(code)
            if not smoke_ok:
                print(f"Smoke test failed: {smoke_msg}")
                error_msg = f"Smoke test: {smoke_msg}"
                metadata["validation_errors"].append(error_msg)
                previous_errors.append(error_msg)
                continue

            print("Smoke test passed")
            print("=== Success ===")
            return True, code, metadata

        # 모든 시도 실패
        error_summary = "\n".join(
            [
                f"Failed after {self.max_retries} attempts.",
                f"Validation errors: {metadata['validation_errors']}",
            ]
        )
        return False, error_summary, metadata

