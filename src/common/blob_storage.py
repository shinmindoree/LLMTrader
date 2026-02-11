from __future__ import annotations

import logging
from typing import Any

from azure.storage.blob import ContainerClient

logger = logging.getLogger(__name__)


class StrategyBlobService:
    """사용자별 전략 코드를 Azure Blob Storage에 저장/조회한다."""

    def __init__(self, connection_string: str, container_name: str = "strategies") -> None:
        self._container = ContainerClient.from_connection_string(connection_string, container_name)
        self._container.create_container()  # idempotent

    def _blob_path(self, user_id: str, strategy_name: str) -> str:
        safe_name = strategy_name.replace("/", "_").replace("\\", "_")
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        return f"{user_id}/{safe_name}"

    def upload(self, user_id: str, strategy_name: str, code: str) -> str:
        path = self._blob_path(user_id, strategy_name)
        self._container.upload_blob(path, code.encode("utf-8"), overwrite=True)
        return path

    def download(self, user_id: str, strategy_name: str) -> str:
        path = self._blob_path(user_id, strategy_name)
        blob = self._container.download_blob(path)
        return blob.readall().decode("utf-8")

    def download_by_path(self, blob_path: str) -> str:
        blob = self._container.download_blob(blob_path)
        return blob.readall().decode("utf-8")

    def list_strategies(self, user_id: str) -> list[dict[str, Any]]:
        prefix = f"{user_id}/"
        blobs = self._container.list_blobs(name_starts_with=prefix)
        result: list[dict[str, Any]] = []
        for blob in blobs:
            name = blob.name[len(prefix):]
            result.append({
                "strategy_name": name,
                "blob_path": blob.name,
                "size": blob.size,
                "last_modified": blob.last_modified.isoformat() if blob.last_modified else None,
            })
        return result

    def delete(self, user_id: str, strategy_name: str) -> bool:
        path = self._blob_path(user_id, strategy_name)
        try:
            self._container.delete_blob(path)
            return True
        except Exception:  # noqa: BLE001
            return False


def get_blob_service() -> StrategyBlobService | None:
    """설정 기반으로 BlobService를 반환. 미설정 시 None."""
    from settings import get_settings

    settings = get_settings()
    conn_str = settings.azure_blob.connection_string.strip()
    if not conn_str:
        return None
    return StrategyBlobService(conn_str, settings.azure_blob.container_name)
