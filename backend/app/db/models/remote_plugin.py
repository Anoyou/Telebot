"""远程 Git 仓库插件登记表（阶段 D：tpm-style 远程插件管理）。

与 ``plugin_install`` 表的区别：
- ``plugin_install`` 记录 zip 上传安装的第三方插件（阶段 B）
- ``remote_plugin`` 记录从远程 Git 仓库 ``git clone`` 安装的插件（本阶段）

二者并行存在；安装目录都是 ``plugins/installed/<name>/``，由 worker loader
统一扫描。运行期靠目录覆盖语义保证唯一加载（loader 同名 key 后写胜出）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class RemotePlugin(Base):
    """远程插件登记表：每行 = 一个从远程仓库克隆下来的第三方插件安装。

    ``name`` 字段同时承担三重身份：
      - 数据库唯一键
      - 文件系统下 ``plugins/installed/<name>/`` 目录名
      - worker loader 注册到 ``_REGISTRY`` 时使用的 plugin key（与 manifest.key 一致）
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
        String(64), nullable=True, comment="最近一次检查到的远程版本"
    )
    update_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="最近一次检查是否发现可更新版本"
    )
    last_update_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近一次检查远程更新的时间"
    )
    last_update_check_error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="最近一次检查更新失败原因"
    )
    lint_warnings: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, comment="安装/更新时静态 lint 警告"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="安装时是否默认为所有账号启用；启用后自动在 AccountFeature 创建行",
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
