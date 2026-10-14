"""System Agent 会话密钥占位符映射列。

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_agent_session",
        sa.Column("secret_map_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("system_agent_session", "secret_map_enc")
