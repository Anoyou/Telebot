"""功能（feature/plugin）与账号-功能关联。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# 内置功能 key（同时 feature.is_builtin = True）
FEATURE_AUTO_REPLY = "auto_reply"
FEATURE_FORWARD = "forward"
FEATURE_SCHEDULER = "scheduler"

# 历史功能 key —— 已在 v0.4.0 砍掉对应 builtin 目录与前端页面，
# 但保留常量用于迁移期间识别 / 清理 DB 旧行（迁移 0014 会清空对应 account_feature 行）
FEATURE_LEGACY_KEYS: tuple[str, ...] = ("group_admin", "monitor")

BUILTIN_FEATURES: dict[str, str] = {
    FEATURE_AUTO_REPLY: "自动回复",
    FEATURE_FORWARD: "消息转发",
    FEATURE_SCHEDULER: "定时任务",
}


# AccountFeature.state
FEATURE_STATE_ACTIVE = "active"
FEATURE_STATE_FAILED = "failed"
FEATURE_STATE_DISABLED = "disabled"


class Feature(Base):
    """功能 / 插件登记表。第三方插件通过 plugin_repo 同步后写入。"""

    __tablename__ = "feature"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AccountFeature(Base):
    """[账号 × 功能] 矩阵的某个格子。"""

    __tablename__ = "account_feature"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(
        String, ForeignKey("feature.key"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String, default=FEATURE_STATE_DISABLED)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
