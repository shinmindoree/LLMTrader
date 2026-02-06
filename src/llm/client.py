"""LLM 클라이언트 - 중계 서버와의 통신 인터페이스."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from src.settings import get_settings


@dataclass
class StrategyGenerationResult:
    """전략 생성 결과."""

    success: bool
    code: str | None = None
    error: str | None = None
    model_used: str | None = None


class LLMClient:
    """LLM 클라이언트 - 중계 서버와 통신."""

    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        """LLM 클라이언트 초기화.

        Args:
            base_url: 중계 서버 기본 URL (None이면 환경 변수에서 읽음)
            timeout: 요청 타임아웃 (초)
        """
        if base_url is None:
            settings = get_settings()
            base_url = settings.relay_server.url
            if not base_url:
                raise ValueError(
                    "RELAY_SERVER_URL 환경 변수가 설정되지 않았습니다. .env 파일에 RELAY_SERVER_URL을 설정해주세요."
                )
            self.api_key = settings.relay_server.api_key
        else:
            self.api_key = ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = 3
        self.retry_delay = 1.0  # 초

    async def health_check(self) -> bool:
        """중계 서버 상태 확인.

        Returns:
            서버 연결 성공 여부
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # /docs 또는 /openapi.json 접근 확인
                response = await client.get(f"{self.base_url}/docs")
                return response.status_code == 200
        except Exception:
            return False

    async def generate_strategy(
        self,
        user_prompt: str,
        messages: list[dict[str, str]] | None = None,
    ) -> StrategyGenerationResult:
        """전략 코드 생성 요청.

        Args:
            user_prompt: 사용자의 자연어 전략 설명 (단일 턴 시 사용)
            messages: 멀티턴 대화 목록 [{"role": "user"|"assistant", "content": "..."}]

        Returns:
            StrategyGenerationResult
        """
        if not messages and (not user_prompt or not user_prompt.strip()):
            return StrategyGenerationResult(
                success=False,
                error="user_prompt가 비어있습니다.",
            )

        payload: dict[str, Any] = {"user_prompt": (user_prompt or "").strip()}
        if messages:
            payload["messages"] = messages

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    headers = {}
                    if self.api_key:
                        headers["X-API-Key"] = self.api_key
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    response = await client.post(
                        f"{self.base_url}/generate",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()

                    data = response.json()

                    # 응답 형식: {"code": "...", "model_used": "..."}
                    code = data.get("code")
                    model_used = data.get("model_used")

                    if not code:
                        return StrategyGenerationResult(
                            success=False,
                            error="서버 응답에 code가 없습니다.",
                            model_used=model_used,
                        )

                    return StrategyGenerationResult(
                        success=True,
                        code=code,
                        model_used=model_used,
                    )

            except httpx.TimeoutException:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                    continue
                return StrategyGenerationResult(
                    success=False,
                    error=f"요청 타임아웃 ({self.timeout}초 초과)",
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 422:
                    # Validation Error
                    try:
                        error_data = e.response.json()
                        detail = error_data.get("detail", [])
                        if detail and isinstance(detail, list) and len(detail) > 0:
                            error_msg = detail[0].get("msg", "Validation error")
                        else:
                            error_msg = "Validation error"
                    except Exception:
                        error_msg = "Validation error"
                    return StrategyGenerationResult(
                        success=False,
                        error=f"요청 형식 오류: {error_msg}",
                    )
                elif e.response.status_code >= 500:
                    # 서버 오류 - 재시도
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    return StrategyGenerationResult(
                        success=False,
                        error=f"서버 오류: {e.response.status_code}",
                    )
                else:
                    return StrategyGenerationResult(
                        success=False,
                        error=f"HTTP 오류: {e.response.status_code}",
                    )

            except httpx.ConnectError:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                    continue
                return StrategyGenerationResult(
                    success=False,
                    error=f"서버 연결 실패: {self.base_url}",
                )

            except Exception as e:
                return StrategyGenerationResult(
                    success=False,
                    error=f"예상치 못한 오류: {str(e)}",
                )

        return StrategyGenerationResult(
            success=False,
            error="최대 재시도 횟수 초과",
        )

    async def summarize_strategy(self, code: str) -> str | None:
        """전략 코드 요약 요청.

        Args:
            code: 전략 Python 코드

        Returns:
            요약 문자열 또는 실패 시 None
        """
        if not code or not code.strip():
            return None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                    headers["Authorization"] = f"Bearer {self.api_key}"
                response = await client.post(
                    f"{self.base_url}/summarize",
                    json={"code": code.strip()},
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                return data.get("summary") or None
        except Exception:
            return None

    async def strategy_chat(
        self,
        code: str,
        summary: str | None,
        messages: list[dict[str, str]],
    ) -> str | None:
        """전략에 대한 질문/설명 요청. 코드 생성 없이 텍스트만 반환."""
        if not code or not code.strip() or not messages:
            return None
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                    headers["Authorization"] = f"Bearer {self.api_key}"
                response = await client.post(
                    f"{self.base_url}/strategy/chat",
                    json={
                        "code": code.strip(),
                        "summary": summary,
                        "messages": messages,
                    },
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                return data.get("content") or None
        except Exception:
            return None

    async def intake_strategy(
        self,
        user_prompt: str,
        messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        """전략 생성 전 입력 정형화/검증 요청."""
        if not messages and (not user_prompt or not user_prompt.strip()):
            return None

        payload: dict[str, Any] = {"user_prompt": (user_prompt or "").strip()}
        if messages:
            payload["messages"] = messages

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key
                    headers["Authorization"] = f"Bearer {self.api_key}"
                response = await client.post(
                    f"{self.base_url}/intake",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict):
                    return data
        except Exception:
            return None
        return None

    async def generate_strategy_stream(
        self,
        user_prompt: str,
        messages: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """전략 코드 생성 스트리밍. Yields {'token': str} or {'done': True} or {'error': str}."""
        payload: dict[str, Any] = {"user_prompt": (user_prompt or "").strip()}
        if messages:
            payload["messages"] = messages
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/generate/stream",
                    json=payload,
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                yield data
                                if data.get("done") or data.get("error"):
                                    return
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            yield {"error": str(e)}
