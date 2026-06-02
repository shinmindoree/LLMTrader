"""add pending_column_drops deferred-drop queue

Revision ID: g2h3i4j5k6l7
Revises: a7c1d2e3f4b5
Create Date: 2026-06-02

Backs the Deferred-Drop mechanism. Instead of dropping a column inline (which
breaks any container still running the previous image — notably the manually
deployed LIVE runner), migrations enqueue the intended drop here via
``control.deferred_drops.enqueue_column_drop``. The deferred-drop cron later
performs the real DROP, but only once every container is confirmed to run the
schema that no longer references the column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g2h3i4j5k6l7"
down_revision: Union[str, None] = "a7c1d2e3f4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_column_drops",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("column_name", sa.String(128), nullable=False),
        sa.Column("enqueued_revision", sa.String(64), nullable=True),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    # At most one *pending* enqueue per (table, column); makes enqueue idempotent.
    op.create_index(
        "uq_pending_column_drops_unprocessed",
        "pending_column_drops",
        ["table_name", "column_name"],
        unique=True,
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pending_column_drops_unprocessed", table_name="pending_column_drops"
    )
    op.drop_table("pending_column_drops")
