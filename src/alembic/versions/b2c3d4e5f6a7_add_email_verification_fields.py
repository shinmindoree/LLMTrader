"""add email_verified and email_verification_token to user_profiles

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "user_profiles",
        sa.Column("email_verification_token", sa.String(128), nullable=True),
    )
    # Mark existing users (e.g. Google OAuth, admin) as verified
    op.execute("UPDATE user_profiles SET email_verified = true WHERE password_hash IS NULL")


def downgrade() -> None:
    op.drop_column("user_profiles", "email_verification_token")
    op.drop_column("user_profiles", "email_verified")
