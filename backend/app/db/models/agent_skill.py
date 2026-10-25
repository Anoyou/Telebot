"""已安装 Agent Skills（兼容 Anthropic Agent Skills 目录格式）。

一个 skill = 一个目录：``SKILL.md``（YAML front matter 含 ``name`` / ``description``）
加可选的附属文本文件。文件内容只作为说明文本保存在数据库里，平台从不执行其中的脚本。

- ``agent_skill``：每行一个已安装 skill，记录来源（GitHub / zip / 粘贴）、提交或内容摘要，
  以及对「系统 Agent」与「插件」的独立启用开关。
- ``agent_skill_file``：skill 目录内的文本文件（含 ``SKILL.md``），按相对路径保存。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

JSONB_VARIANT = JSON().with_variant(postgresql.JSONB(), "postgresql")

SKILL_SOURCE_GITHUB = "github"
SKILL_SOURCE_ZIP_URL = "zip_url"
SKILL_SOURCE_ZIP_UPLOAD = "zip_upload"
SKILL_SOURCE_PASTE = "paste"
SKILL_SOURCE_MD_UPLOAD = "md_upload"
# 随插件分发的内置 skill：不可卸载，可停用；用户编辑保存为覆盖层，插件升级不覆盖。
SKILL_SOURCE_PLUGIN_BUNDLED = "plugin_bundled"
SKILL_SOURCES = frozenset(
    {
        SKILL_SOURCE_GITHUB,
        SKILL_SOURCE_ZIP_URL,
        SKILL_SOURCE_ZIP_UPLOAD,
        SKILL_SOURCE_PASTE,
        SKILL_SOURCE_MD_UPLOAD,
    }
)
# 可以直接编辑 SKILL.md 的来源（远程来源编辑后会被“更新”覆盖，因此不允许）
SKILL_EDITABLE_SOURCES = frozenset(
    {SKILL_SOURCE_PLUGIN_BUNDLED, SKILL_SOURCE_PASTE, SKILL_SOURCE_MD_UPLOAD, SKILL_SOURCE_ZIP_UPLOAD}
)
# 可以从原地址重新拉取更新的来源
SKILL_REMOTE_SOURCES = frozenset({SKILL_SOURCE_GITHUB, SKILL_SOURCE_ZIP_URL})


class AgentSkill(Base):
    """一个已安装的 skill。"""

    __tablename__ = "agent_skill"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    license: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled_agent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    enabled_plugins: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_files: Mapped[list[Any] | None] = mapped_column(JSONB_VARIANT, nullable=True)
    installed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 内置（插件分发）skill：所属插件、上游版本与用户覆盖层状态
    owner_plugin: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_modified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    upstream_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upstream_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 用户编辑/确认时所基于的上游摘要；与 upstream_sha256 不同即“内置有新版本”
    base_upstream_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upstream_files: Mapped[dict[str, Any] | None] = mapped_column(JSONB_VARIANT, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentSkillFile(Base):
    """skill 目录内的一个文本文件。"""

    __tablename__ = "agent_skill_file"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    skill_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("agent_skill.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("skill_id", "path", name="uq_agent_skill_file_path"),)


__all__ = [
    "SKILL_REMOTE_SOURCES",
    "SKILL_SOURCES",
    "SKILL_EDITABLE_SOURCES",
    "SKILL_SOURCE_GITHUB",
    "SKILL_SOURCE_MD_UPLOAD",
    "SKILL_SOURCE_PLUGIN_BUNDLED",
    "SKILL_SOURCE_PASTE",
    "SKILL_SOURCE_ZIP_UPLOAD",
    "SKILL_SOURCE_ZIP_URL",
    "AgentSkill",
    "AgentSkillFile",
]
