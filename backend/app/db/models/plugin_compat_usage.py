"""兼容层使用统计（第 22 轮）：每个 [兼容项 × 插件] 一行，累计命中次数与首次 / 最近命中时间。

由 :mod:`app.services.plugin_compat_usage` 从各进程内存批量 upsert（SQLite / PostgreSQL 通用），
只做统计，不影响任何兼容层行为。时间用 Unix 秒（浮点），与插件钩子统计口径一致。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PluginCompatUsage(Base):
    __tablename__ = "plugin_compat_usage"

    compat_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    plugin_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    first_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_at: Mapped[float] = mapped_column(Float, nullable=False)


__all__ = ["PluginCompatUsage"]
