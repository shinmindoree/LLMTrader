from __future__ import annotations

import logging
import os
from typing import Any

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

logger = logging.getLogger(__name__)


class StrategyBlobService:
    """사용자별 전략 코드를 Azure Blob Storage에 저장/조회한다."""

    def __init__(self, container: ContainerClient) -> None:
        self._container = container
        try:
            self._container.create_container()
        except ResourceExistsError:
            pass

    @classmethod
    def from_connection_string(cls, connection_string: str, container_name: str = "strategies") -> StrategyBlobService:
        return cls(ContainerClient.from_connection_string(connection_string, container_name))

    @classmethod
    def from_account_url(cls, account_url: str, container_name: str = "strategies") -> StrategyBlobService:
        kwargs: dict[str, Any] = {}
        client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
        if client_id:
            kwargs["managed_identity_client_id"] = client_id
        credential = DefaultAzureCredential(**kwargs)
        return cls(ContainerClient(account_url=account_url, container_name=container_name, credential=credential))

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

    def delete_by_path(self, blob_path: str) -> bool:
        try:
            self._container.delete_blob(blob_path)
            return True
        except Exception:  # noqa: BLE001
            return False


def get_blob_service() -> StrategyBlobService | None:
    """설정 기반으로 BlobService를 반환. 미설정 시 None."""
    from settings import get_settings

    settings = get_settings()
    account_url = settings.azure_blob.account_url.strip()
    conn_str = settings.azure_blob.connection_string.strip()
    if account_url:
        return StrategyBlobService.from_account_url(account_url, settings.azure_blob.container_name)
    if not conn_str:
        return None
    return StrategyBlobService.from_connection_string(conn_str, settings.azure_blob.container_name)
