"""LLM 클라이언트 — relay 모듈 직접 호출 (HTTP hop 제거).

이전: API → httpx → Relay HTTP → Azure OpenAI (4-hop SSE)
현재: API → relay 모듈 직접 호출 → Azure OpenAI (직접 호출)
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from relay.azure_openai import chat_completion, chat_completion_messages, chat_completion_stream
from relay.capability_registry import (
    SUPPORTED_CONTEXT_METHODS,
    SUPPORTED_DATA_SOURCES,
    SUPPORTED_INDICATOR_SCOPES,
    UNSUPPORTED_CAPABILITY_RULES,
    capability_summary_lines,
)
from relay.config import RelayConfig, get_config
from relay.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    TEST_SYSTEM_PROMPT,
    build_analyst_system_prompt,
    build_intake_system_prompt,
    build_repair_system_prompt,
    build_strategy_chat_system_prompt,
)

logger = logging.getLogger(__name__)


@dataclass
class StrategyGenerationResult:
    """전략 생성 결과."""

    success: bool
    code: str | None = None
    error: str | None = None
    model_used: str | None = None


@dataclass
class StrategyRepairResult:
    """전략 수정 결과."""

    success: bool
    code: str | None = None
    error: str | None = None
    model_used: str | None = None


def _get_relay_config() -> RelayConfig:
    """Get relay config, raising ValueError if not configured."""
    config = get_config()
    if not config.is_azure_configured():
        raise ValueError("Azure OpenAI not configured (missing OPENAI_BASE_URL / OPENAI_MODEL)")
    return config


class LLMClient:
    """LLM 클라이언트 — relay 모듈을 직접 호출하여 Azure OpenAI와 통신."""

    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        # base_url과 timeout은 하위호환 시그니처 유지용. 실제로는 relay config 사용.
        self._config = _get_relay_config()

    async def health_check(self) -> bool:
        """Azure OpenAI 설정 확인."""
        return self._config.is_azure_configured()

    async def generate_strategy(
        self,
        user_prompt: str,
        messages: list[dict[str, str]] | None = None,
    ) -> StrategyGenerationResult:
        """전략 코드 생성 (비스트리밍). generate_strategy_stream 사용 권장."""
        if not messages and (not user_prompt or not user_prompt.strip()):
            return StrategyGenerationResult(success=False, error="user_prompt가 비어있습니다.")

        # Collect all tokens from the stream path
        code_acc: list[str] = []
        repaired = False
        try:
            async for event in self.generate_strategy_stream(user_prompt, messages):
                if "error" in event:
                    return StrategyGenerationResult(success=False, error=str(event["error"]))
                if "token" in event:
                    code_acc.append(event["token"])
                if event.get("done"):
                    if event.get("code"):
                        return StrategyGenerationResult(
                            success=True,
                            code=event["code"],
                            model_used=event.get("model_used"),
                        )
                    repaired = event.get("repaired", False)
                    break
        except Exception as e:
            return StrategyGenerationResult(success=False, error=f"생성 실패: {e}")

        code = "".join(code_acc).strip()
        if code:
            return StrategyGenerationResult(success=True, code=code)
        return StrategyGenerationResult(success=False, error="빈 코드 생성")

    async def summarize_strategy(self, code: str) -> str | None:
        """전략 코드 요약."""
        if not code or not code.strip():
            return None
        try:
            content, _ = chat_completion(
                self._config,
                system_content=SUMMARY_SYSTEM_PROMPT,
                user_content=code.strip(),
                model=self._config.resolved_summarizer_model,
            )
            return content.strip() if content else None
        except Exception:
            logger.warning("summarize_strategy failed", exc_info=True)
            return None

    async def strategy_chat(
        self,
        code: str,
        summary: str | None,
        messages: list[dict[str, str]],
    ) -> str | None:
        """전략 채팅 (비스트리밍)."""
        if not messages:
            return None
        try:
            system_content = build_strategy_chat_system_prompt(code or "", summary)
            content, _ = chat_completion_messages(
                self._config,
                system_content=system_content,
                messages=messages,
            )
            if not content or not content.strip():
                return None
            return content.strip()
        except Exception:
            logger.warning("strategy_chat failed", exc_info=True)
            return None

    async def strategy_chat_stream(
        self,
        code: str,
        summary: str | None,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[dict[str, Any]]:
        """전략 채팅 스트리밍 — relay 직접 호출 (httpx hop 제거)."""
        if not messages:
            yield {"error": "messages are required"}
            return

        try:
            system_content = build_strategy_chat_system_prompt(code or "", summary)
            acc: list[str] = []
            async for token in chat_completion_stream(
                self._config,
                system_content=system_content,
                messages=messages,
            ):
                acc.append(token)
                yield {"token": token}

            full_text = "".join(acc)
            # Check for model refusal
            from relay.main import _is_model_refusal, _NON_TRADING_REJECTION_MSG

            if _is_model_refusal(full_text):
                yield {"refusal_replace": _NON_TRADING_REJECTION_MSG}
            yield {"done": True}
        except Exception as e:
            logger.exception("strategy_chat_stream failed: %s", e)
            yield {"error": str(e)}

    async def analyze_backtest(
        self,
        code: str,
        backtest_results: str,
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        """백테스트 결과 분석 (analyst 모델 직접 호출)."""
        if not code or not backtest_results:
            return None

        user_content = (
            f"Strategy code:\n```python\n{code.strip()}\n```\n\n"
            f"Backtest results:\n{backtest_results.strip()}"
        )
        if summary:
            user_content += f"\n\nStrategy summary:\n{summary}"

        try:
            content, _ = chat_completion(
                self._config,
                system_content=build_analyst_system_prompt(),
                user_content=user_content,
                model=self._config.resolved_analyst_model,
                text_format={"type": "json_object"},
            )
            if not content or not content.strip():
                return None
            try:
                return json.loads(content.strip())
            except json.JSONDecodeError:
                return {"raw_analysis": content.strip()}
        except Exception:
            logger.warning("analyze_backtest failed", exc_info=True)
            return None

    async def intake_strategy(
        self,
        user_prompt: str,
        messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        """전략 생성 전 입력 정형화/검증."""
        if not messages and (not user_prompt or not user_prompt.strip()):
            return None

        try:
            system_content = build_intake_system_prompt()
            if messages:
                content, _ = chat_completion_messages(
                    self._config,
                    system_content=system_content,
                    messages=messages,
                )
            else:
                content, _ = chat_completion(
                    self._config,
                    system_content=system_content,
                    user_content=(user_prompt or "").strip(),
                )
            if not content or not content.strip():
                return None

            from relay.main import _extract_json_object, _sanitize_intake_response, ChatMessage

            payload = _extract_json_object(content) or {}
            chat_messages = (
                [ChatMessage(role=m["role"], content=m["content"]) for m in messages]
                if messages
                else []
            )
            result = _sanitize_intake_response(
                payload,
                prompt=(user_prompt or "").strip(),
                messages=chat_messages,
            )
            return result.model_dump() if hasattr(result, "model_dump") else dict(result)
        except Exception:
            logger.warning("intake_strategy failed", exc_info=True)
            return None

    async def strategy_capabilities(self) -> dict[str, Any] | None:
        """전략 생성 가능 범위(지원/비지원 capability) 조회."""
        try:
            return {
                "supported_data_sources": list(SUPPORTED_DATA_SOURCES),
                "supported_indicator_scopes": list(SUPPORTED_INDICATOR_SCOPES),
                "supported_context_methods": list(SUPPORTED_CONTEXT_METHODS),
                "unsupported_categories": [r.name for r in UNSUPPORTED_CAPABILITY_RULES],
                "summary_lines": capability_summary_lines(),
            }
        except Exception:
            return None

    async def repair_strategy(
        self,
        code: str,
        verification_error: str,
        user_prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
    ) -> StrategyRepairResult:
        """검증 실패 코드를 자동 수정."""
        if not code or not code.strip():
            return StrategyRepairResult(success=False, error="code가 비어있습니다.")
        if not verification_error or not verification_error.strip():
            return StrategyRepairResult(success=False, error="verification_error가 비어있습니다.")

        prompt_parts = [
            "Fix the strategy code based on the verification failure below.",
            "",
            f"Verification error:\n{verification_error.strip()}",
        ]
        if user_prompt and user_prompt.strip():
            prompt_parts.extend(["", f"Original user request:\n{user_prompt.strip()}"])
        if messages:
            compact_msgs = [m for m in messages if (m.get("content") or "").strip()]
            if compact_msgs:
                prompt_parts.extend([
                    "",
                    "Conversation context (JSON):",
                    json.dumps(compact_msgs, ensure_ascii=False),
                ])
        prompt_parts.extend(["", "Current code:", code.strip()])

        try:
            content, model_used = chat_completion(
                self._config,
                system_content=build_repair_system_prompt(),
                user_content="\n".join(prompt_parts),
                model=self._config.resolved_reviewer_model,
            )
            if not content or not content.strip():
                return StrategyRepairResult(success=False, error="빈 수정 결과")
            return StrategyRepairResult(success=True, code=content.strip(), model_used=model_used)
        except Exception as e:
            return StrategyRepairResult(success=False, error=f"자동 수정 실패: {e}")

    async def generate_strategy_stream(
        self,
        user_prompt: str,
        messages: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """전략 코드 생성 스트리밍 — relay _generate_stream_body 직접 호출."""
        from relay.main import StrategyRequest, ChatMessage as RelayChatMessage, _generate_stream_body

        relay_messages = (
            [RelayChatMessage(role=m["role"], content=m["content"]) for m in messages]
            if messages
            else None
        )
        body = StrategyRequest(
            user_prompt=(user_prompt or "").strip(),
            messages=relay_messages,
        )

        try:
            async for sse_line in _generate_stream_body(body):
                # _generate_stream_body yields SSE formatted strings: "data: {...}\n\n"
                line = sse_line.strip()
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        yield data
                        if data.get("done") or data.get("error"):
                            return
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.exception("generate_strategy_stream failed: %s", e)
            yield {"error": str(e)}

    async def test_llm(self, input_text: str) -> tuple[str | None, str | None]:
        """LLM 연결 테스트."""
        text = (input_text or "").strip() or "Hello"
        try:
            content, _ = chat_completion(
                self._config,
                system_content=TEST_SYSTEM_PROMPT,
                user_content=text,
            )
            return (content.strip() if content else None, None)
        except Exception as e:
            return (None, str(e))
