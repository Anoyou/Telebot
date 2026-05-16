"""账号绑定普通 Bot 的配置与授权用户。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

ACCOUNT_BOT_STATUS_DISABLED = "disabled"
ACCOUNT_BOT_STATUS_RUNNING = "running"
ACCOUNT_BOT_STATUS_ERROR = "error"
ACCOUNT_BOT_STATUS_STOPPED = "stopped"

ACCOUNT_BOT_ROLE_VIEWER = "viewer"
ACCOUNT_BOT_ROLE_OPERATOR = "operator"
ACCOUNT_BOT_ROLE_ADMIN = "admin"
ACCOUNT_BOT_ROLES = {
    ACCOUNT_BOT_ROLE_VIEWER,
    ACCOUNT_BOT_ROLE_OPERATOR,
    ACCOUNT_BOT_ROLE_ADMIN,
}


class AccountBot(Base):
    """每个 UserBot 账号绑定一个普通 Bot API token。"""

    __tablename__ = "account_bot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    bot_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ACCOUNT_BOT_STATUS_DISABLED
    )
    last_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remote_plugin_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AccountBotUser(Base):
    """账号 Bot 的授权 TG 用户，按账号隔离。"""

    __tablename__ = "account_bot_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ACCOUNT_BOT_ROLE_VIEWER
    )
    notify_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("account_id", "tg_user_id", name="uq_account_bot_user_account_tg"),
        Index("ix_account_bot_user_account_enabled", "account_id", "enabled"),
        Index("ix_account_bot_user_tg_user_id", "tg_user_id"),
    )
