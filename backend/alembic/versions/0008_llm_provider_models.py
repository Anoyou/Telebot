"""add models list to llm_provider

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-04

为 LLMProvider 加 ``models`` JSONB 数组列：

- 单 provider 可启用多个模型（参考 alma 的"模型选择"交互）
- ``default_model`` 字段保留不变；只是语义上变成"自动路由的兜底"
- 老数据自动回填：把 ``default_model`` 写入 ``models[0]``，并标 ``enabled=true``
  这样升级后已有 provider 仍能在下游"展开式 select"里正常显示
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_provider
            ADD COLUMN models JSONB NOT NULL DEFAULT '[]'::jsonb;

        -- 老数据回填：把 default_model 写入 models[0]，标 enabled=true，custom=true
        -- （custom=true 因为是历史人工填的，不是 fetch 来的）
        UPDATE llm_provider
           SET models = jsonb_build_array(
                 jsonb_build_object(
                   'id', default_model,
                   'enabled', true,
                   'custom', true,
                   'label', NULL
                 )
               )
         WHERE jsonb_array_length(models) = 0
           AND default_model <> '';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_provider DROP COLUMN IF EXISTS models;
        """
    )
