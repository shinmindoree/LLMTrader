"""애플리케이션 기동 시 Alembic 마이그레이션 실행 (배포 후 스키마 누락 방지)."""

from __future__ import annotations

from pathlib import Path


def run_alembic_upgrade_head() -> None:
    """저장소 루트의 alembic.ini를 사용해 `upgrade head`를 동기 실행한다."""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    ini_path = repo_root / "alembic.ini"
    if not ini_path.is_file():
        raise FileNotFoundError(f"alembic.ini not found at {ini_path}")

    cfg = Config(str(ini_path))
    command.upgrade(cfg, "head")
