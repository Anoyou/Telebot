"""add device_profile table and account.device_profile_id

Revision ID: 0003b
Revises: 0003
Create Date: 2026-05-03

引入"设备伪装库"概念：
- 新表 ``device_profile`` 存多条设备伪装配置（device_model / system_version / app_version /
  lang_code / system_lang_code），可被账号引用。
- ``account`` 加一列 ``device_profile_id`` 外键指向上表，可空（空时回落到全局默认）。
- 同时插入 3 条预置 profile（macOS / iPhone / Windows Desktop），把 macOS 置为 ``is_default``。

注意：device_profile 的修改只对**新登录**的 session 生效。Telegram 把设备名绑在
auth_key 上，已有 session 想让 TG 端显示新设备名必须重新登录走 wizard。

迁移编号说明：本迁移最初命名为 ``0003``（down_revision=0002），与 Sprint 2 #2
的 ``0003_command_template`` 构成 alembic 分叉。汇总时把本迁移线性化到 0003
之后，改名 ``0003b``，让链变成：
``0002 → 0003 (command) → 0003b (device) → 0004 (ignored) → 0005 (plugin)``。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003b"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_profile",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("device_model", sa.String(length=128), nullable=False),
        sa.Column("system_version", sa.String(length=64), nullable=False),
        sa.Column("app_version", sa.String(length=64), nullable=False),
        sa.Column("lang_code", sa.String(length=16), nullable=False, server_default="zh"),
        sa.Column(
            "system_lang_code",
            sa.String(length=16),
            nullable=False,
            server_default="zh-Hans",
        ),
        # is_default：全表只允许至多一条 true。约束在应用层（API）维护，避免 PG 部分唯一索引方言差异。
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_device_profile_name", "device_profile", ["name"])

    # account.device_profile_id：空 = 走系统默认 profile
    op.add_column(
        "account",
        sa.Column("device_profile_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_account_device_profile",
        "account",
        "device_profile",
        ["device_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 预置 3 条 profile；macOS 设为默认
    op.execute(
        """
        INSERT INTO device_profile
            (name, device_model, system_version, app_version, lang_code, system_lang_code, is_default)
        VALUES
            ('macOS Telegram', 'MacBook Pro', 'macOS 14.5', 'Telegram macOS 11.5', 'zh', 'zh-Hans', true),
            ('iPhone Telegram', 'iPhone', 'iOS 17.4', 'Telegram iOS 11.5.0', 'zh', 'zh-Hans', false),
            ('Windows Telegram Desktop', 'PC', 'Windows 11', 'Telegram Desktop 5.5.4', 'zh', 'zh-Hans', false);
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_account_device_profile", "account", type_="foreignkey")
    op.drop_column("account", "device_profile_id")
    op.drop_index("ix_device_profile_name", table_name="device_profile")
    op.drop_table("device_profile")
