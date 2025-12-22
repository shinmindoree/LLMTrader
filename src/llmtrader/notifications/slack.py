"""Slack Incoming Webhook 기반 알림 전송."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SlackNotifier:
    """Slack Incoming Webhook 알림기.

    참고:
    - Incoming Webhook URL은 비밀값이므로 Azure Container Apps에서는 'Secrets'로 저장 후
      환경변수로 주입하는 것을 권장합니다.
    """

    webhook_url: str
    timeout: float = 5.0

    async def send(self, text: str) -> None:
        if not self.webhook_url:
            return
        payload = {"text": text}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(self.webhook_url, json=payload)
            r.raise_for_status()



