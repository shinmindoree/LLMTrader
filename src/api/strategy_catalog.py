from __future__ import annotations

from pathlib import Path


def list_strategy_files(strategy_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for d in strategy_dirs:
        if not d.exists():
            continue
        for p in d.glob("*_strategy.py"):
            if p.name == "generated_strategy.py":
                continue
            if p.is_file():
                files.append(p)
    # stable order
    files_sorted = sorted({p.resolve() for p in files}, key=lambda p: p.name)
    return files_sorted


def validate_strategy_path(
    *,
    repo_root: Path,
    strategy_dirs: list[Path],
    strategy_path: str,
) -> Path:
    raw = (strategy_path or "").strip()
    if not raw:
        raise ValueError("strategy_path is required")
    if raw.endswith(".py") is False or raw.endswith("_strategy.py") is False:
        raise ValueError("strategy_path must be a *_strategy.py file")

    candidate = (repo_root / raw).resolve()
    if not candidate.exists() or not candidate.is_file():
        raise ValueError("strategy_path not found")

    for d in strategy_dirs:
        try:
            candidate.relative_to(d.resolve())
            return candidate
        except ValueError:
            continue
    raise ValueError("strategy_path is outside STRATEGY_DIRS")

