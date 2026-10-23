"""插件安装记录模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, String, Text, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# PG 生产库为 JSONB（与迁移终态一致）；SQLite（测试/本地）回落到通用 JSON
JSONB_VARIANT = JSON().with_variant(postgresql.JSONB(), "postgresql")

PLUGIN_SOURCE_BUILTIN = "builtin"
PLUGIN_SOURCE_ZIP = "zip"
PLUGIN_SOURCE_REPO = "repo"
PLUGIN_SOURCE_GIT = "git"
PLUGIN_SOURCE_LOCAL = "local"
# 仅用于读取升级前的历史安装记录；所有新写入必须使用 repo/git/local/zip。
PLUGIN_SOURCE_LEGACY = "official"

PLUGIN_TRUST_CORE = "core"
PLUGIN_TRUST_VERIFIED = "verified"
PLUGIN_TRUST_COMMUNITY = "community"
PLUGIN_TRUST_LOCAL = "local"
PLUGIN_TRUST_ORPHAN = "orphan"


class PluginInstall(Base):
    """旧版 zip 安装表。

    Deprecated: 新写路径已切到 ``InstalledPlugin``；本模型仅保留给升级兼容和只读排查。
    """

    __tablename__ = "plugin_install"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="0.0.0")
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB_VARIANT, nullable=True)
    signature_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    installed_path: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        Index("ix_plugin_install_source", "source"),
        Index("ix_plugin_install_enabled", "enabled"),
    )


class InstalledPlugin(Base):
    """统一安装记录表；插件安装、启停、卸载的权威数据源。"""

    __tablename__ = "installed_plugin"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="0.0.0")
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB_VARIANT, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signature_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    trust_tier: Mapped[str] = mapped_column(String(32), nullable=False, default=PLUGIN_TRUST_COMMUNITY)
    source_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_install_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_load_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lint_warnings: Mapped[list[str]] = mapped_column(JSONB_VARIANT, nullable=False, default=list)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 与 0026 迁移对齐：来源 / 启停 / 信任层级的筛选索引
    __table_args__ = (
        Index("ix_installed_plugin_source", "source"),
        Index("ix_installed_plugin_enabled", "enabled"),
        Index("ix_installed_plugin_trust_tier", "trust_tier"),
    )


class PluginInstallHistory(Base):
    """插件安装生命周期事件；卸载后仍保留，供审计与版本对比。"""

    __tablename__ = "plugin_install_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plugin_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    signature_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_plugin_install_history_key_created", "plugin_key", "created_at"),
    )


__all__ = [
    "InstalledPlugin",
    "PLUGIN_SOURCE_BUILTIN",
    "PLUGIN_SOURCE_GIT",
    "PLUGIN_SOURCE_LOCAL",
    "PLUGIN_SOURCE_LEGACY",
    "PLUGIN_SOURCE_REPO",
    "PLUGIN_SOURCE_ZIP",
    "PLUGIN_TRUST_COMMUNITY",
    "PLUGIN_TRUST_CORE",
    "PLUGIN_TRUST_LOCAL",
    "PLUGIN_TRUST_ORPHAN",
    "PLUGIN_TRUST_VERIFIED",
    "PluginInstall",
    "PluginInstallHistory",
]
