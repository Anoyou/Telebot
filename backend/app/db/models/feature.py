"""功能（feature/plugin）与账号-功能关联。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ...feature_registry import BUILTIN_FEATURES  # noqa: F401 - 兼容旧导入路径的 re-export
from ..base import Base

# PG 生产库为 JSONB（与 0001 迁移终态一致）；SQLite（测试/本地）回落到通用 JSON
JSONB_VARIANT = JSON().with_variant(postgresql.JSONB(), "postgresql")

# ── 历史功能常量（各处 import 用；不再有新增必要，以后新 builtin 直接建目录即可）──
FEATURE_AUTO_REPLY = "auto_reply"
FEATURE_FORWARD = "forward"
FEATURE_SCHEDULER = "scheduler"
FEATURE_GAME24 = "game24"
FEATURE_AUTOREPEAT = "autorepeat"
FEATURE_CODEX_IMAGE = "codex_image"

# AccountFeature.state
FEATURE_STATE_ACTIVE = "active"
FEATURE_STATE_FAILED = "failed"
FEATURE_STATE_DISABLED = "disabled"


class Feature(Base):
    """功能 / 插件登记表。第三方插件通过 plugin_repo 同步后写入。"""

    __tablename__ = "feature"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=True)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB_VARIANT, nullable=True)


class AccountFeature(Base):
    """[账号 × 功能] 矩阵的某个格子。"""

    __tablename__ = "account_feature"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(
        Text, ForeignKey("feature.key"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB_VARIANT, default=dict, nullable=True)
    state: Mapped[str] = mapped_column(Text, default=FEATURE_STATE_DISABLED, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
