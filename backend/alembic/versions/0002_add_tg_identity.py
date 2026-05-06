"""add tg_user_id and tg_username to account

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-03

新增字段：
- ``account.tg_user_id``  Telegram 用户数字 ID（来自 ``client.get_me().id``）
- ``account.tg_username`` Telegram 用户名（不含 ``@``，可为空，用户可在 TG 客户端随时修改）

两列均可空：旧账号在重新登录或 worker 下次启动时回填，不阻塞历史数据。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "account",
        sa.Column("tg_username", sa.String(length=64), nullable=True),
    )
    # username 不是唯一（用户可改名后被别人占用），但建索引方便后台搜索
    op.create_index(
        "ix_account_tg_username",
        "account",
        ["tg_username"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_account_tg_username", table_name="account")
    op.drop_column("account", "tg_username")
    op.drop_column("account", "tg_user_id")
