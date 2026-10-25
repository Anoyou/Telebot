"""已安装 Agent Skills：agent_skill + agent_skill_file

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-26

新增两张表保存管理员安装的 Agent Skills（Anthropic Agent Skills 目录格式）：

- ``agent_skill``：skill 元数据、来源（GitHub / zip / 粘贴 / 上传 SKILL.md / 插件内置）与
  提交/内容摘要，对系统 Agent、插件分别启用的开关，以及插件内置 skill 的上游版本与用户覆盖层状态。
- ``agent_skill_file``：skill 目录内的文本文件，随 skill 级联删除。

纯新增表，不改动既有数据；downgrade 删除两张表（会丢失已安装的 skills）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "agent_skill",
        sa.Column("id", _ID, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("license", sa.String(200), nullable=True),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("source_commit", sa.String(64), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("enabled_agent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled_plugins", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_files", _JSON, nullable=True),
        sa.Column("installed_by", sa.BigInteger(), nullable=True),
        sa.Column("owner_plugin", sa.String(128), nullable=True),
        sa.Column("user_modified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("upstream_version", sa.String(64), nullable=True),
        sa.Column("upstream_sha256", sa.String(64), nullable=True),
        sa.Column("base_upstream_sha256", sa.String(64), nullable=True),
        sa.Column("upstream_files", _JSON, nullable=True),
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
    op.create_index("ix_agent_skill_name", "agent_skill", ["name"], unique=True)
    op.create_table(
        "agent_skill_file",
        sa.Column("id", _ID, primary_key=True, autoincrement=True),
        sa.Column(
            "skill_id",
            _ID,
            sa.ForeignKey("agent_skill.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("skill_id", "path", name="uq_agent_skill_file_path"),
    )
    op.create_index("ix_agent_skill_file_skill_id", "agent_skill_file", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_skill_file_skill_id", table_name="agent_skill_file")
    op.drop_table("agent_skill_file")
    op.drop_index("ix_agent_skill_name", table_name="agent_skill")
    op.drop_table("agent_skill")
