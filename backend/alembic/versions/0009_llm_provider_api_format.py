"""add api_format to llm_provider

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-04

为 LLMProvider 加 ``api_format`` 列：

- 'chat_completions'    POST /chat/completions     OpenAI 经典协议
- 'responses'           POST /responses            OpenAI 2024 出的新协议
- 'anthropic_messages'  POST /messages             Anthropic 协议

老数据自动回填：
- ``provider=anthropic`` → ``anthropic_messages``
- 其它（openai / ollama）→ ``chat_completions``

升级后行为完全不变；用户根据自己反代的实际支持去 UI 改。
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_provider
            ADD COLUMN api_format VARCHAR(32)
                NOT NULL DEFAULT 'chat_completions';

        -- 已有 anthropic provider 自动给 anthropic_messages
        UPDATE llm_provider
           SET api_format = 'anthropic_messages'
         WHERE provider = 'anthropic';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE llm_provider DROP COLUMN IF EXISTS api_format;")
