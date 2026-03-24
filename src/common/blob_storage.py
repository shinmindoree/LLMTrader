from __future__ import annotations

import logging
import os
from typing import Any

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import ContainerClient

logger = logging.getLogger(__name__)


class StrategyBlobService:
    """사용자별 전략 코드를 Azure Blob Storage에 저장/조회한다."""

    def __init__(self, container: ContainerClient) -> None:
        self._container = container

    @classmethod
    def from_connection_string(cls, connection_string: str, container_name: str = "strategies") -> StrategyBlobService:
        return cls(ContainerClient.from_connection_string(connection_string, container_name))

    @classmethod
    def from_account_url(cls, account_url: str, container_name: str = "strategies") -> StrategyBlobService:
        client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
        # Container Apps / Azure 환경에서는 ManagedIdentityCredential 우선 사용
        if os.getenv("IDENTITY_ENDPOINT"):
            kwargs: dict[str, Any] = {}
            if client_id:
                kwargs["client_id"] = client_id
            credential = ManagedIdentityCredential(**kwargs)
            logger.info("Using ManagedIdentityCredential for Azure Blob Storage")
        else:
            kwargs = {}
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


_blob_service_cache: StrategyBlobService | None = None
_blob_service_initialized = False


def get_blob_service() -> StrategyBlobService | None:
    """설정 기반으로 BlobService를 반환. 미설정 시 None. 인스턴스를 캐싱한다."""
    global _blob_service_cache, _blob_service_initialized  # noqa: PLW0603
    if _blob_service_initialized:
        return _blob_service_cache

    from settings import get_settings

    settings = get_settings()
    account_url = settings.azure_blob.account_url.strip()
    conn_str = settings.azure_blob.connection_string.strip()
    try:
        if account_url:
            svc = StrategyBlobService.from_account_url(account_url, settings.azure_blob.container_name)
        elif conn_str:
            svc = StrategyBlobService.from_connection_string(conn_str, settings.azure_blob.container_name)
        else:
            svc = None

        # 접근 가능한지 가볍게 검증 (list_blobs 1개만 조회)
        if svc is not None:
            next(svc._container.list_blobs(results_per_page=1).by_page(), None)
            logger.info("Azure Blob Storage connected successfully")
            _blob_service_cache = svc
    except StopIteration:
        # 빈 컨테이너 — 정상
        logger.info("Azure Blob Storage connected (empty container)")
        _blob_service_cache = svc  # type: ignore[possibly-undefined]
    except Exception:  # noqa: BLE001
        logger.error("Failed to initialize Azure Blob service", exc_info=True)
        _blob_service_cache = None
    _blob_service_initialized = True
    return _blob_service_cache
