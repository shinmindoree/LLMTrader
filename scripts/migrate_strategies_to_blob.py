"""Local strategy files -> Supabase Storage + strategy_metadata migration.

Usage:
    python scripts/migrate_strategies_to_blob.py --user-id <user-id> --dry-run
    python scripts/migrate_strategies_to_blob.py --user-id <user-id>
    python scripts/migrate_strategies_to_blob.py --mapping-file strategy_user_map.json

Notes:
    - Requires Supabase storage settings (`SUPABASE_URL` or `SUPABASE_STORAGE_URL`, `SUPABASE_SERVICE_ROLE_KEY`).
    - Requires DB connectivity so `strategy_metadata` can be updated.
    - `--user-id` applies the same owner to every local strategy file.
    - `--mapping-file` should be a JSON object like:
      {
        "rsi_strategy.py": "user-a",
        "macd_strategy.py": "user-b"
      }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker

from api.strategy_catalog import list_strategy_files
from common.strategy_storage import get_strategy_storage
from control.db import create_async_engine, init_db
from control.repo import get_user_profile, upsert_strategy_meta
from settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _resolve_strategy_dirs() -> list[Path]:
    settings = get_settings()
    raw = settings.strategy_dirs
    repo_root = Path(__file__).resolve().parents[1]
    dirs: list[Path] = []
    for chunk in raw.split(","):
        value = chunk.strip()
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (repo_root / path).resolve()
        dirs.append(path)
    return dirs


def _load_mapping(mapping_file: Path | None) -> dict[str, str]:
    if mapping_file is None:
        return {}
    data = json.loads(mapping_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("mapping file must be a JSON object of filename -> user_id")
    mapping: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("mapping file entries must be string -> string")
        mapping[key.strip()] = value.strip()
    return mapping


async def _ensure_user_exists(session_maker: async_sessionmaker, user_id: str) -> None:
    async with session_maker() as session:
        profile = await get_user_profile(session, user_id=user_id)
    if profile is None:
        raise ValueError(f"user_id not found in user_profiles: {user_id}")


async def main(
    *,
    user_id: str | None,
    mapping_file: Path | None,
    dry_run: bool,
) -> None:
    storage = get_strategy_storage()
    if storage is None:
        raise ValueError(
            "Supabase Storage is not configured. Set SUPABASE_URL or SUPABASE_STORAGE_URL and SUPABASE_SERVICE_ROLE_KEY first."
        )

    mapping = _load_mapping(mapping_file)
    if not user_id and not mapping:
        raise ValueError("either --user-id or --mapping-file is required")

    settings = get_settings()
    engine = create_async_engine(settings.effective_database_url)
    await init_db(engine)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    files = list_strategy_files(_resolve_strategy_dirs())
    if not files:
        logger.info("No local strategy files found.")
        await engine.dispose()
        return

    user_ids = {uid for uid in mapping.values() if uid}
    if user_id:
        user_ids.add(user_id)
    for resolved_user_id in sorted(user_ids):
        await _ensure_user_exists(session_maker, resolved_user_id)

    migrated = 0
    skipped = 0
    failed = 0

    for strategy_file in files:
        owner = mapping.get(strategy_file.name) or user_id
        if not owner:
            skipped += 1
            logger.warning("SKIP %s: no user_id mapping", strategy_file.name)
            continue

        try:
            code = strategy_file.read_text(encoding="utf-8")
            blob_path = storage._object_path(owner, strategy_file.name)
            if dry_run:
                logger.info("DRY RUN upload %s -> %s", strategy_file.name, blob_path)
                migrated += 1
                continue

            uploaded_path = storage.upload(owner, strategy_file.name, code)
            async with session_maker() as session:
                await upsert_strategy_meta(
                    session,
                    user_id=owner,
                    strategy_name=strategy_file.name,
                    blob_path=uploaded_path,
                )
                await session.commit()
            migrated += 1
            logger.info("OK %s -> %s", strategy_file.name, uploaded_path)
        except Exception:
            failed += 1
            logger.exception("FAIL %s", strategy_file.name)

    await engine.dispose()
    mode = "DRY RUN" if dry_run else "COMMITTED"
    logger.info(
        "Strategy migration complete (%s): migrated=%d skipped=%d failed=%d",
        mode,
        migrated,
        skipped,
        failed,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate local strategy files into Supabase Storage")
    parser.add_argument("--user-id", help="Default owner for every local strategy file")
    parser.add_argument(
        "--mapping-file",
        type=Path,
        help="JSON object mapping strategy filename to user_id",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview uploads without writing blob/DB")
    args = parser.parse_args()
    asyncio.run(main(user_id=args.user_id, mapping_file=args.mapping_file, dry_run=args.dry_run))
