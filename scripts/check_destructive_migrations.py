#!/usr/bin/env python3
"""Fail CI when a newly-added Alembic migration contains a destructive operation.

Why: dropping or renaming a column/table that is still mapped by a not-yet-redeployed
container (most often the manually-deployed LIVE runner) breaks its SQLAlchemy ORM —
every SELECT of that table raises UndefinedColumnError and LIVE jobs FAIL silently.

Policy: migrations must be **additive only** (Expand/Contract). To remove a column,
enqueue a deferred drop instead of dropping it inline:

    from control.deferred_drops import enqueue_column_drop
    enqueue_column_drop("user_profiles", "binance_api_key_enc", revision=revision)

The deferred-drop cron (cleanup-pending-column-drops.yml) performs the real DROP later,
only once every container is confirmed to be running the schema that no longer uses it.

Escape hatch: if a destructive op is genuinely the Contract phase and every container
is already past the Expand release, append this exact comment on the offending line:

    op.drop_column("t", "c")  # migration-guard: allow-destructive

Usage:
    python scripts/check_destructive_migrations.py [--base <git-ref>]

Env (CI): BASE_REF overrides the diff base (defaults to origin/main).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

VERSIONS_DIR = "src/alembic/versions/"
ALLOW_MARKER = "migration-guard: allow-destructive"

# (regex, human description)
DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bop\.drop_column\s*\("), "op.drop_column"),
    (re.compile(r"\bop\.drop_table\s*\("), "op.drop_table"),
    (re.compile(r"\bnew_column_name\s*="), "op.alter_column(... rename) via new_column_name"),
]


def _run_git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _added_migration_files(base: str) -> list[str]:
    """Return migration files ADDED relative to `base` (filter=A)."""
    try:
        out = _run_git(["diff", "--diff-filter=A", "--name-only", f"{base}...HEAD"])
    except subprocess.CalledProcessError:
        # Fallback: a plain two-dot diff if the merge-base form is unavailable.
        out = _run_git(["diff", "--diff-filter=A", "--name-only", base])
    files = [f.strip() for f in out.splitlines() if f.strip()]
    return [
        f
        for f in files
        if f.startswith(VERSIONS_DIR) and f.endswith(".py") and "__init__" not in f
    ]


_DOWNGRADE_RE = re.compile(r"^\s*def\s+downgrade\s*\(")


def _scan_file(path: Path) -> list[str]:
    """Scan only the ``upgrade()`` body.

    ``downgrade()`` is inherently destructive (it reverses an additive upgrade)
    and only runs on an explicit rollback, so its drops are expected and ignored.
    """
    violations: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if _DOWNGRADE_RE.match(raw):
            break
        if ALLOW_MARKER in raw:
            continue
        for pattern, desc in DESTRUCTIVE_PATTERNS:
            if pattern.search(raw):
                violations.append(f"{path}:{lineno}: {desc} -> {raw.strip()}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=os.environ.get("BASE_REF", "origin/main"),
        help="git ref to diff against (default: env BASE_REF or origin/main)",
    )
    args = parser.parse_args()

    added = _added_migration_files(args.base)
    if not added:
        print(f"No newly-added migrations vs {args.base}; nothing to check.")
        return 0

    print(f"Checking {len(added)} new migration(s) vs {args.base}:")
    for f in added:
        print(f"  - {f}")

    all_violations: list[str] = []
    for f in added:
        p = Path(f)
        if p.is_file():
            all_violations.extend(_scan_file(p))

    if not all_violations:
        print("\nOK: no destructive operations found in new migrations.")
        return 0

    print("\n❌ Destructive migration operation(s) detected:\n", file=sys.stderr)
    for v in all_violations:
        print(f"  {v}", file=sys.stderr)
    print(
        "\nMigrations must be additive (Expand/Contract). To remove a column use:\n"
        "    from control.deferred_drops import enqueue_column_drop\n"
        '    enqueue_column_drop("table", "column", revision=revision)\n'
        "\nIf this IS an intentional, safe Contract-phase drop (every container is\n"
        f"already past the Expand release), append `# {ALLOW_MARKER}` to the line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
