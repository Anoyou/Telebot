"""Web 端用户表（单用户系统，但仍用表保存用户名/密码哈希/TOTP）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WebUser(Base):
    """Web 后台登录用户。系统单用户，但表结构保留扩展可能。"""

    __tablename__ = "web_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # 密码版本号：每次改密 +1，用于让旧 JWT（携带旧版本）自动失效
    pwd_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # TOTP secret 用 master_key 加密后存（为空表示未启用 2FA）
    totp_secret_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
