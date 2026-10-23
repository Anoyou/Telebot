"""系统级配置 + 通知通道。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Text, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# PG 生产库为 JSONB（与 0001 迁移终态一致）；SQLite（测试/本地）回落到通用 JSON
JSONB_VARIANT = JSON().with_variant(postgresql.JSONB(), "postgresql")


class SystemSetting(Base):
    """key-value 系统配置（command_prefix、kill_switch、timezone 等）。"""

    __tablename__ = "system_setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB_VARIANT, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )


class NotificationChannel(Base):
    """通知通道：email / webhook / tg_self（自发到收藏夹）。"""

    __tablename__ = "notification_channel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    # config 内敏感字段需在写入前由 service 层加密
    config: Mapped[dict[str, Any]] = mapped_column(JSONB_VARIANT, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=True)
