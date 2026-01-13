"""LLM 클라이언트 - 중계 서버와의 통신 인터페이스."""

import asyncio
from dataclasses import dataclass

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

    async def generate_strategy(self, user_prompt: str) -> StrategyGenerationResult:
        """전략 코드 생성 요청.

        Args:
            user_prompt: 사용자의 자연어 전략 설명

        Returns:
            StrategyGenerationResult
        """
        if not user_prompt or not user_prompt.strip():
            return StrategyGenerationResult(
                success=False,
                error="user_prompt가 비어있습니다.",
            )

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/generate",
                        json={"user_prompt": user_prompt.strip()},
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
