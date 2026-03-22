"""add jobs.live_heartbeat_at for runner stale-live detection

Revision ID: 9c0a1b2d3e4f
Revises: 8be195835568
Create Date: 2026-03-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9c0a1b2d3e4f"
down_revision: Union[str, None] = "8be195835568"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("live_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_live_heartbeat_at", "jobs", ["live_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_live_heartbeat_at", table_name="jobs")
    op.drop_column("jobs", "live_heartbeat_at")
