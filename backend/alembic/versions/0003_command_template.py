"""add command_template / account_command_link / llm_provider

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-03

新增 3 张表（Sprint2 #2 自定义命令）：

- ``command_template``       全局命令模板库（``,name`` 触发）
- ``account_command_link``   [账号 × 模板] 启用关系
- ``llm_provider``           LLM 供应商（OpenAI/Anthropic/Ollama），api_key 加密落库

注意：
- 与 ``0004_ignored_peer.py`` 是 Sprint 2 并行会话的独立分支（都 down_revision=0002）；
  合并到 main 时由汇总人决定 chain 顺序，本会话不动 0004。
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- ────────────────────────────────────────────────────────────
        -- 命令模板（全局，每条 = 一个 ,name 命令的"配方"）
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE command_template (
            id              BIGSERIAL PRIMARY KEY,
            name            VARCHAR(64) NOT NULL UNIQUE,
            type            VARCHAR(16) NOT NULL,
            config          JSONB NOT NULL DEFAULT '{}'::jsonb,
            description     VARCHAR(255),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        -- ────────────────────────────────────────────────────────────
        -- [账号 × 模板] 启用关系；联合主键
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE account_command_link (
            account_id      BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            template_id     BIGINT NOT NULL REFERENCES command_template(id) ON DELETE CASCADE,
            enabled         BOOLEAN NOT NULL DEFAULT true,
            PRIMARY KEY (account_id, template_id)
        );
        CREATE INDEX ix_account_command_link_account ON account_command_link (account_id);

        -- ────────────────────────────────────────────────────────────
        -- LLM 供应商（AI 命令调用入口）
        --   api_key_enc 必须是 Fernet 加密结果（见 app/crypto.py）
        --   GET 接口仅返回 has_api_key:bool，不返明文
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE llm_provider (
            id              BIGSERIAL PRIMARY KEY,
            name            VARCHAR(64) NOT NULL UNIQUE,
            provider        VARCHAR(16) NOT NULL,
            api_key_enc     TEXT,
            base_url        VARCHAR(255),
            default_model   VARCHAR(64) NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS account_command_link CASCADE;
        DROP TABLE IF EXISTS llm_provider CASCADE;
        DROP TABLE IF EXISTS command_template CASCADE;
        """
    )
