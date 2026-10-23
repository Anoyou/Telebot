"""账号查重索引 + 审计/用量表查询索引。

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-24

- ``account.phone`` / ``account.tg_user_id``：登录绑定向导的应用层查重守卫
  （login_service 新建账号前按 phone/tg_user_id 查重）需要查询索引。
  ⚠ 这里刻意只建普通 btree 索引、不上唯一约束：存量库可能已有同手机号/
  同 tg_user_id 的重复行，直接建唯一索引会让启动迁移失败。唯一约束需等
  存量重复数据清理后再单独上（本批只做应用层守卫 + 查询索引）。
- ``audit_log.ts``：/api/logs/audit 按 ts 过滤+倒序排序，此前无二级索引。
  审计日志按合规要求不做自动删除，只加查询索引。
- ``llm_usage.source``：服务 ``source LIKE 'plugin:%'`` 前缀聚合，
  用 text_pattern_ops 让 LIKE 前缀匹配走索引。
"""

from __future__ import annotations

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_account_phone", "account", ["phone"], unique=False)
    op.create_index("ix_account_tg_user_id", "account", ["tg_user_id"], unique=False)
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"], unique=False)
    op.create_index(
        "ix_llm_usage_source",
        "llm_usage",
        ["source"],
        unique=False,
        postgresql_ops={"source": "text_pattern_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_source", table_name="llm_usage")
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_index("ix_account_tg_user_id", table_name="account")
    op.drop_index("ix_account_phone", table_name="account")
