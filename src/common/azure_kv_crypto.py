from __future__ import annotations

import base64
import json
import logging
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class AzureKeyVaultCryptoService:
    """Azure Key Vault envelope encryption 서비스.

    1. 데이터마다 랜덤 Fernet DEK(Data Encryption Key) 생성
    2. DEK로 평문 암호화 (Fernet)
    3. Azure Key Vault의 RSA CMK(Customer Managed Key)로 DEK 암호화
    4. DB 저장: JSON { "encrypted_dek": ..., "ciphertext": ... }
    5. 복호화: KV로 DEK 복호화 -> Fernet으로 데이터 복호화
    """

    def __init__(self, settings: Any) -> None:
        vault_url = settings.azure_keyvault.url.strip()
        key_name = settings.azure_keyvault.key_name.strip()
        if not vault_url or not key_name:
            raise ValueError("AZURE_KEYVAULT_URL and AZURE_KEYVAULT_KEY_NAME are required")

        credential = DefaultAzureCredential()
        key_client = KeyClient(vault_url=vault_url, credential=credential)
        key = key_client.get_key(key_name)
        self._crypto_client = CryptographyClient(key, credential=credential)

    def encrypt(self, plaintext: str) -> str:
        dek = Fernet.generate_key()
        fernet = Fernet(dek)
        ciphertext = fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

        result = self._crypto_client.encrypt(EncryptionAlgorithm.rsa_oaep_256, dek)
        encrypted_dek = base64.b64encode(result.ciphertext).decode("ascii")

        envelope = json.dumps({"v": 2, "encrypted_dek": encrypted_dek, "ciphertext": ciphertext})
        return base64.b64encode(envelope.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token.encode("ascii")).decode("utf-8")
        envelope = json.loads(raw)

        encrypted_dek_bytes = base64.b64decode(envelope["encrypted_dek"])
        result = self._crypto_client.decrypt(EncryptionAlgorithm.rsa_oaep_256, encrypted_dek_bytes)
        dek = result.plaintext

        fernet = Fernet(dek)
        return fernet.decrypt(envelope["ciphertext"].encode("ascii")).decode("utf-8")
