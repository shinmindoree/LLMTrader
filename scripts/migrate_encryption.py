"""Fernet -> Azure Key Vault 암호화 마이그레이션 스크립트.

Usage:
    CRYPTO_BACKEND=azure_kv python scripts/migrate_encryption.py --dry-run
    CRYPTO_BACKEND=azure_kv python scripts/migrate_encryption.py

기존 Fernet으로 암호화된 바이낸스 API 키를 Azure Key Vault envelope encryption으로 재암호화한다.
- ENCRYPTION_KEYS 환경변수 (기존 Fernet 키)와 Azure KV 설정이 모두 필요하다.
- --dry-run 옵션으로 먼저 테스트 후 실행을 권장한다.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from control.models import UserProfile
from settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main(dry_run: bool = True) -> None:
    settings = get_settings()

    from common.crypto import FernetCryptoService
    fernet_keys = settings.encryption.key_list
    if not fernet_keys:
        logger.error("ENCRYPTION_KEYS (Fernet) is not configured. Cannot decrypt old data.")
        return
    old_crypto = FernetCryptoService(fernet_keys)

    from common.azure_kv_crypto import AzureKeyVaultCryptoService
    new_crypto = AzureKeyVaultCryptoService(settings)

    engine = create_async_engine(settings.effective_database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.binance_api_key_enc.is_not(None))
        )
        profiles = list(result.scalars().all())

    logger.info("Found %d profiles with encrypted keys", len(profiles))
    migrated = 0
    failed = 0

    for profile in profiles:
        try:
            api_key = old_crypto.decrypt(profile.binance_api_key_enc)
            api_secret = old_crypto.decrypt(profile.binance_api_secret_enc)

            new_key_enc = new_crypto.encrypt(api_key)
            new_secret_enc = new_crypto.encrypt(api_secret)

            verify_key = new_crypto.decrypt(new_key_enc)
            verify_secret = new_crypto.decrypt(new_secret_enc)
            assert verify_key == api_key, "Key verification failed"
            assert verify_secret == api_secret, "Secret verification failed"

            if not dry_run:
                async with sm() as session:
                    await session.execute(
                        update(UserProfile)
                        .where(UserProfile.user_id == profile.user_id)
                        .values(
                            binance_api_key_enc=new_key_enc,
                            binance_api_secret_enc=new_secret_enc,
                        )
                    )
                    await session.commit()

            migrated += 1
            logger.info("  [OK] user_id=%s %s", profile.user_id, "(dry-run)" if dry_run else "")
        except Exception:
            failed += 1
            logger.exception("  [FAIL] user_id=%s", profile.user_id)

    await engine.dispose()

    mode = "DRY RUN" if dry_run else "COMMITTED"
    logger.info("Migration complete (%s): migrated=%d, failed=%d", mode, migrated, failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Fernet -> Azure KV encryption")
    parser.add_argument("--dry-run", action="store_true", help="Preview without committing changes")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
