from __future__ import annotations

import logging
from typing import Protocol

from cryptography.fernet import Fernet, MultiFernet

logger = logging.getLogger(__name__)


class CryptoServiceProtocol(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


class FernetCryptoService:
    """Fernet 대칭 키 암호화 서비스 (MultiFernet 키 로테이션 지원)."""

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("At least one encryption key is required")
        self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")


def generate_fernet_key() -> str:
    """새 Fernet 키를 생성한다. 초기 설정 시 사용."""
    return Fernet.generate_key().decode("ascii")


def get_crypto_service() -> CryptoServiceProtocol:
    """설정 기반으로 적절한 CryptoService를 반환한다."""
    from settings import get_settings

    settings = get_settings()
    backend = settings.crypto_backend.strip().lower()

    if backend == "azure_kv":
        return _get_azure_kv_service(settings)

    keys = settings.encryption.key_list
    if not keys:
        raise ValueError(
            "ENCRYPTION_KEYS is not configured. "
            "Generate a key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    return FernetCryptoService(keys)


def _get_azure_kv_service(settings: object) -> CryptoServiceProtocol:
    """Azure Key Vault envelope encryption (Phase 4)."""
    from common.azure_kv_crypto import AzureKeyVaultCryptoService

    return AzureKeyVaultCryptoService(settings)
