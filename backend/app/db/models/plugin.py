"""插件安装记录模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

PLUGIN_SOURCE_BUILTIN = "builtin"
PLUGIN_SOURCE_ZIP = "zip"
PLUGIN_SOURCE_REPO = "repo"


class PluginInstall(Base):
    __tablename__ = "plugin_install"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False, default="0.0.0")
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    signature_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    installed_path: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "PLUGIN_SOURCE_BUILTIN",
    "PLUGIN_SOURCE_REPO",
    "PLUGIN_SOURCE_ZIP",
    "PluginInstall",
]
