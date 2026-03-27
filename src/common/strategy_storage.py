from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class StrategyStorage(Protocol):
    """전략 스토리지 공통 인터페이스."""

    def upload(self, user_id: str, strategy_name: str, code: str) -> str: ...
    def download_by_path(self, object_path: str) -> str: ...
    def delete_by_path(self, object_path: str) -> bool: ...


def get_strategy_storage() -> StrategyStorage | None:
    """설정 기반으로 전략 스토리지를 반환.

    우선순위: Azure Blob → None (로컬 파일시스템 폴백).
    """
    from common.blob_storage import get_blob_service

    try:
        blob = get_blob_service()
        if blob is not None:
            return blob
    except Exception:  # noqa: BLE001
        logger.error("Azure Blob service unavailable", exc_info=True)

    return None
