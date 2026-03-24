from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@runtime_checkable
class StrategyStorage(Protocol):
    """전략 스토리지 공통 인터페이스."""

    def upload(self, user_id: str, strategy_name: str, code: str) -> str: ...
    def download_by_path(self, object_path: str) -> str: ...
    def delete_by_path(self, object_path: str) -> bool: ...


class StrategyObjectStorage:
    """Supabase Storage 기반 전략 코드 저장소."""

    def __init__(self, *, base_url: str, service_role_key: str, bucket_name: str = "strategies") -> None:
        self._base_url = base_url.rstrip("/")
        self._service_role_key = service_role_key
        self._bucket_name = bucket_name
        self._headers = {
            "apikey": service_role_key,
        }
        self._ensure_bucket()

    def _bucket_url(self, suffix: str = "") -> str:
        base = f"{self._base_url}/storage/v1"
        return f"{base}/{suffix.lstrip('/')}" if suffix else base

    def _object_path(self, user_id: str, strategy_name: str) -> str:
        safe_name = strategy_name.replace("/", "_").replace("\\", "_")
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        return f"{user_id}/{safe_name}"

    def _object_url(self, path: str) -> str:
        encoded = quote(path, safe="/")
        return self._bucket_url(f"object/{self._bucket_name}/{encoded}")

    def _ensure_bucket(self) -> None:
        get_resp = httpx.get(
            self._bucket_url(f"bucket/{quote(self._bucket_name, safe='')}"),
            headers=self._headers,
            timeout=20.0,
        )
        if get_resp.status_code == 200:
            return
        if get_resp.status_code != 404:
            get_resp.raise_for_status()

        create_resp = httpx.post(
            self._bucket_url("bucket"),
            headers={**self._headers, "Content-Type": "application/json"},
            json={"id": self._bucket_name, "name": self._bucket_name, "public": False},
            timeout=20.0,
        )
        if create_resp.status_code not in (200, 201, 409):
            create_resp.raise_for_status()

    def upload(self, user_id: str, strategy_name: str, code: str) -> str:
        path = self._object_path(user_id, strategy_name)
        response = httpx.post(
            self._object_url(path),
            headers={
                **self._headers,
                "Content-Type": "text/x-python; charset=utf-8",
                "x-upsert": "true",
            },
            content=code.encode("utf-8"),
            timeout=30.0,
        )
        response.raise_for_status()
        return path

    def download_by_path(self, object_path: str) -> str:
        response = httpx.get(
            self._object_url(object_path),
            headers=self._headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.text

    def delete_by_path(self, object_path: str) -> bool:
        response = httpx.delete(
            self._object_url(object_path),
            headers=self._headers,
            timeout=30.0,
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True


def get_strategy_storage() -> StrategyStorage | None:
    """설정 기반으로 전략 스토리지를 반환.

    우선순위: Azure Blob → Supabase Storage → None (로컬 파일시스템 폴백).
    """
    from common.blob_storage import get_blob_service
    from settings import get_settings

    # 1) Azure Blob Storage (우선)
    blob = get_blob_service()
    if blob is not None:
        logger.info("Strategy storage: Azure Blob Storage")
        return blob

    # 2) Supabase Storage (폴백)
    settings = get_settings()
    base_url = (settings.supabase_storage.url or settings.supabase_auth.url).strip()
    service_role_key = settings.supabase_storage.service_role_key.strip()
    bucket_name = settings.supabase_storage.bucket_name.strip() or "strategies"
    if base_url and service_role_key:
        logger.info("Strategy storage: Supabase Storage")
        return StrategyObjectStorage(
            base_url=base_url,
            service_role_key=service_role_key,
            bucket_name=bucket_name,
        )

    return None
