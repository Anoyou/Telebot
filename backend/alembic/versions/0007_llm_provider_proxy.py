"""add proxy_id to llm_provider

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-04

为「LLM 调用走代理」追加一列 ``llm_provider.proxy_id``：

- 外键指向 ``proxy.id``，``ON DELETE SET NULL`` —— 删代理不会级联删 LLM provider，只是
  让该 provider 退化成"直连"。
- NULL = 直连（前端的 "DIRECT — 不走代理" 选项）。
- 索引 ``ix_llm_provider_proxy_id`` 给 reload 时按 proxy_id 反查准备（虽然现在没用到，
  但量起来后会需要）。

mtproxy 类型的 proxy 行不能给 LLM 用——HTTP 客户端不支持 MTProto；schema 层会拒绝。
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_provider
            ADD COLUMN proxy_id BIGINT
                REFERENCES proxy(id) ON DELETE SET NULL;

        CREATE INDEX ix_llm_provider_proxy_id ON llm_provider (proxy_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_llm_provider_proxy_id;
        ALTER TABLE llm_provider
            DROP COLUMN IF EXISTS proxy_id;
        """
    )
