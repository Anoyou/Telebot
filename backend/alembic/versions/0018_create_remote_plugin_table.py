"""create remote_plugin table

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-08

新增表：``remote_plugin``
- 阶段 D：从远程 Git 仓库克隆安装的第三方插件登记表
- ``name`` 唯一约束：同名插件全局只允许一份安装；升级走 UPDATE
- 与 ``plugin_install``（zip 安装）表共存；运行期统一靠 worker loader
  扫描 ``plugins/installed/`` 加载，二者互不干扰
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_plugin",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("author", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False, server_default="0.0.0"),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_remote_plugin_name",
        "remote_plugin",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_remote_plugin_name", table_name="remote_plugin")
    op.drop_table("remote_plugin")
