"""Deferred column-drop queue helpers.

Dropping a column that is still mapped by a not-yet-redeployed container breaks
that container's SQLAlchemy ORM (every SELECT of the table raises
UndefinedColumnError). The LIVE runner is deployed manually and routinely lags,
so an inline ``op.drop_column`` in an auto-applied migration is the exact bug
that took down live trading on 2026-06-02.

Instead, migrations call :func:`enqueue_column_drop` (additive: the column is
kept, a row is queued). The deferred-drop cron later calls
:func:`process_pending_column_drops`, which performs the real DROP only after an
out-of-band gate has confirmed every container runs the new schema.

Both functions are intentionally dependency-light (raw SQL via a SQLAlchemy
Connection) so they work inside Alembic migrations and standalone scripts alike.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, kind: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe {kind} identifier: {name!r}")
    return name


def enqueue_column_drop(
    table_name: str,
    column_name: str,
    *,
    revision: str | None = None,
    note: str | None = None,
) -> None:
    """Queue a column drop instead of dropping it now (call from a migration).

    Idempotent: a partial unique index prevents duplicate *pending* rows for the
    same (table, column). The column itself is left in place.
    """
    from alembic import op

    _validate_identifier(table_name, "table")
    _validate_identifier(column_name, "column")

    bind = op.get_bind()
    bind.execute(
        text(
            """
            INSERT INTO pending_column_drops
                (table_name, column_name, enqueued_revision, note)
            VALUES (:t, :c, :rev, :note)
            ON CONFLICT DO NOTHING
            """
        ),
        {"t": table_name, "c": column_name, "rev": revision, "note": note},
    )


@dataclass
class DropResult:
    table_name: str
    column_name: str
    action: str  # "dropped" | "already_absent"


def process_pending_column_drops(conn: Connection) -> list[DropResult]:
    """Execute all unprocessed queued drops. Idempotent and safe to re-run.

    For each pending row it checks ``information_schema.columns``; if the column
    still exists it is dropped, otherwise it is treated as already gone. Either
    way the row is marked processed. Intended to run only after the caller has
    verified every container is on the new schema.
    """
    rows = conn.execute(
        text(
            """
            SELECT id, table_name, column_name
            FROM pending_column_drops
            WHERE processed_at IS NULL
            ORDER BY id
            """
        )
    ).fetchall()

    results: list[DropResult] = []
    for row_id, table_name, column_name in rows:
        _validate_identifier(table_name, "table")
        _validate_identifier(column_name, "column")

        exists = conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :t AND column_name = :c
                """
            ),
            {"t": table_name, "c": column_name},
        ).first()

        if exists:
            conn.execute(
                text(f'ALTER TABLE "{table_name}" DROP COLUMN IF EXISTS "{column_name}"')
            )
            action = "dropped"
        else:
            action = "already_absent"

        conn.execute(
            text("UPDATE pending_column_drops SET processed_at = NOW() WHERE id = :id"),
            {"id": row_id},
        )
        results.append(DropResult(table_name, column_name, action))

    return results
