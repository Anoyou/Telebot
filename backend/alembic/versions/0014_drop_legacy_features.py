"""drop legacy feature rows: group_admin / monitor

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-06

v0.4.0 砍掉 group_admin / monitor 两个 builtin 插件目录后，
DB 里残留的 ``account_feature`` 与 ``feature`` 行需要清掉，
否则前端"功能矩阵 / 账号详情"还会读到这两个 key 渲染老 UI。

清理范围：
- account_feature 行（CASCADE 因素：account_id 是 FK，留行不删账号无害但不洁净）
- feature 主表行（也是这两个 key）

回滚（downgrade）只重建空的 feature 行（无 builtin display_name 用本 key 占位），
account_feature 数据不再回灌，因为根本没有"恢复用户启用状态"的语义。
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


_LEGACY_KEYS = ("group_admin", "monitor")


def upgrade() -> None:
    op.execute(
        f"DELETE FROM account_feature WHERE feature_key IN {_LEGACY_KEYS!r};"
    )
    op.execute(
        f"DELETE FROM feature WHERE key IN {_LEGACY_KEYS!r};"
    )


def downgrade() -> None:
    # 只重建主表占位行；account_feature 数据无法恢复
    op.execute(
        """
        INSERT INTO feature (key, display_name, is_builtin) VALUES
            ('group_admin', '群组管理', true),
            ('monitor',     '消息监控', true)
        ON CONFLICT (key) DO NOTHING;
        """
    )
