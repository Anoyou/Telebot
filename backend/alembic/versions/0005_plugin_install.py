"""add plugin_install table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-03

新增表：``plugin_install``
- 第三方插件（zip 上传 / repo 拉取）的安装记录
- ``key`` 与 manifest.key 一致；同时也是 worker 加载时的目录名
- ``signature_ok`` 三态：True/False/NULL（NULL 表示未提供 .sig 文件）
- ``installed_path`` 是解压后的绝对路径（一般在 ``data/plugins/installed/<key>``）
- 阶段 C 的 ``repo_id`` 外键指向 ``plugin_repo.id``，从仓库安装时填写

设计要点：
- ``key`` 作主键 → 一个 key 全局只能有一份安装；升级走 UPDATE 而非新行
- ``enabled`` 单列：装上是一回事，启用是另一回事；签名失败时强制 enabled=false 直到管理员确认
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE plugin_install (
            key             TEXT PRIMARY KEY,
            source          TEXT NOT NULL,
            version         TEXT NOT NULL DEFAULT '0.0.0',
            manifest_json   JSONB,
            signature_ok    BOOLEAN,
            installed_path  TEXT NOT NULL,
            enabled         BOOLEAN NOT NULL DEFAULT false,
            repo_id         BIGINT REFERENCES plugin_repo(id) ON DELETE SET NULL,
            installed_at    TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX ix_plugin_install_source ON plugin_install (source);
        CREATE INDEX ix_plugin_install_enabled ON plugin_install (enabled);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS plugin_install CASCADE;
        """
    )
