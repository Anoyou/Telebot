"""兼容层使用统计：plugin_compat_usage

Revision ID: 0065
Revises: 0064
Create Date: 2026-11-01

第 22 轮：给插件兼容层（docs/PLUGIN-API-1.0-PLAN.md 第 3 节 C01–C20）加使用计数。
每个 [兼容项 × 插件] 一行，累计命中次数与首次 / 最近命中时间（Unix 秒）。

纯新增表，不改动既有数据；downgrade 删除该表（只丢失统计）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_compat_usage",
        sa.Column("compat_id", sa.String(16), nullable=False),
        sa.Column("plugin_key", sa.String(128), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("first_at", sa.Float(), nullable=False),
        sa.Column("last_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("compat_id", "plugin_key", name="pk_plugin_compat_usage"),
    )


def downgrade() -> None:
    op.drop_table("plugin_compat_usage")
