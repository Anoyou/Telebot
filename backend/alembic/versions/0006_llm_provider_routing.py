"""add routing fields to llm_provider

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-03

为 LLM 自动路由（按消息类型选模型）追加四个字段到 ``llm_provider``：

- ``modality``     模态（text / vision / audio / multimodal）；默认 text
- ``tags``         JSONB list[str] 路由标签；路由器据此选 provider
- ``cost_tier``    1=cheap / 2=mid / 3=premium；默认 2
- ``notes``        运维备注（仅给自己看）

只 ALTER TABLE 加列，不改 PK 也不影响老数据；
全部带 server_default，老行升级时自动填默认值，无需 backfill 脚本。
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TABLE 一次到位；JSONB 默认 '[]' 让老行自动得到空数组
    op.execute(
        """
        ALTER TABLE llm_provider
            ADD COLUMN modality   VARCHAR(16) NOT NULL DEFAULT 'text',
            ADD COLUMN tags       JSONB       NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN cost_tier  INTEGER     NOT NULL DEFAULT 2,
            ADD COLUMN notes      VARCHAR(500);

        -- 给已存在的老 provider 行打基础标签，避免上线后路由空转
        -- 仅对存量行生效，新行走 server_default
        UPDATE llm_provider
           SET tags = '["chat"]'::jsonb
         WHERE jsonb_array_length(tags) = 0;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_provider
            DROP COLUMN IF EXISTS modality,
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS cost_tier,
            DROP COLUMN IF EXISTS notes;
        """
    )
