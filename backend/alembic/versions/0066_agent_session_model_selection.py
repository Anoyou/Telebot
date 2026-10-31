"""Agent 会话记住上次选择的模型：system_agent_session.model_selection

Revision ID: 0066
Revises: 0065
Create Date: 2026-11-01

第 22 轮：每个 Agent 会话记住上次选择的本轮模型（含执行方式 / 客户端身份），
退出重进、切换会话、换设备后恢复。新增一个可空 JSON 列，旧数据为 NULL（= 自动）；
downgrade 删除该列（只丢失「上次选择」，不影响会话与消息）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_agent_session",
        sa.Column("model_selection", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("system_agent_session", "model_selection")
