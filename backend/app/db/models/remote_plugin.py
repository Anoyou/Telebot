"""远程 Git 仓库插件登记表（legacy 只读快照）。

Deprecated: 新的安装、启停、卸载与更新状态都以 ``installed_plugin`` 为权威来源。
本模型暂时保留给升级兼容、历史排查和下一个 major 前的只读访问。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class RemotePlugin(Base):
    """旧版远程插件登记表：每行 = 一个从远程仓库克隆下来的第三方插件安装快照。

    ``name`` 字段同时承担三重身份：
      - 数据库唯一键
      - 文件系统下 ``plugins/installed/<name>/`` 目录名
      - worker loader 注册到 ``_REGISTRY`` 时使用的 plugin key（与 manifest.key 一致）

    Deprecated: 写路径已切到 ``InstalledPlugin``，不要在新代码中更新本表。
    """

    __tablename__ = "remote_plugin"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="0.0.0")
    latest_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    update_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    last_update_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_update_check_error: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    lint_warnings: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["RemotePlugin"]
