"""add pwd_version to web_user

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-04

为 web_user 增加 ``pwd_version``（默认 0，非空），用于 JWT 版本校验：
- token 里带 ``pwd_v``
- 改密后 ``pwd_version += 1``
- 旧 token 自动失效
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE web_user
            ADD COLUMN pwd_version BIGINT NOT NULL DEFAULT 0;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE web_user DROP COLUMN IF EXISTS pwd_version;")
