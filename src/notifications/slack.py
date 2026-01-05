"""Slack Incoming Webhook 기반 알림 전송."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class SlackNotifier:
    """Slack Incoming Webhook 알림기.

    참고:
    - Incoming Webhook URL은 비밀값이므로 환경변수로 관리
      환경변수로 주입하는 것을 권장합니다.
    """

    webhook_url: str
    timeout: float = 5.0

    async def send(self, text: str, color: str | None = None) -> None:
        """Slack 메시지 전송.

        Args:
            text: 메시지 텍스트
            color: 색상 (예: "good"=녹색, "danger"=빨간색, "#36a64f"=녹색 hex, "#ff0000"=빨간색 hex)
        """
        if not self.webhook_url:
            return
        
        # 색상이 지정된 경우 attachments 사용 (색상 표시)
        if color:
            payload: dict[str, Any] = {
                "attachments": [
                    {
                        "color": color,
                        "text": text,
                    }
                ]
            }
        else:
            # 색상이 없으면 기본 텍스트 메시지
            payload: dict[str, Any] = {"text": text}
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(self.webhook_url, json=payload)
            r.raise_for_status()






