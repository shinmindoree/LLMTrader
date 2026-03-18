from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class StrategyObjectStorage:
    """Supabase Storage 기반 전략 코드 저장소."""

    def __init__(self, *, base_url: str, service_role_key: str, bucket_name: str = "strategies") -> None:
        self._base_url = base_url.rstrip("/")
        self._service_role_key = service_role_key
        self._bucket_name = bucket_name
        self._headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
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


def get_strategy_storage() -> StrategyObjectStorage | None:
    """설정 기반으로 전략 스토리지를 반환. 미설정 시 None."""
    from settings import get_settings

    settings = get_settings()
    base_url = (settings.supabase_storage.url or settings.supabase_auth.url).strip()
    service_role_key = settings.supabase_storage.service_role_key.strip()
    bucket_name = settings.supabase_storage.bucket_name.strip() or "strategies"
    if not base_url or not service_role_key:
        return None
    return StrategyObjectStorage(
        base_url=base_url,
        service_role_key=service_role_key,
        bucket_name=bucket_name,
    )
