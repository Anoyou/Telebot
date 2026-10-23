"""规则表：从属于 [账号 × 功能]。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# PG 生产库为 JSONB（与 0001 迁移终态一致）；SQLite（测试/本地）回落到通用 JSON
JSONB_VARIANT = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Rule(Base):
    """统一规则表，按 feature_key 区分语义（关键词/cron/源-目标等）。"""

    __tablename__ = "rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    feature_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB_VARIANT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (Index("ix_rule_account_feature_enabled", "account_id", "feature_key", "enabled"),)
