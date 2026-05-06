"""被账号忽略的 Telegram peer（私聊 / 群 / 频道）。

每个账号维护一份"忽略名单"。worker 收到来自这些 peer 的 incoming 消息时
会直接短路所有插件分发——不消耗任何风控配额、也不触发 auto_reply 等回调。

设计说明：
- ``peer_id`` 是 Telethon 的 ``event.chat_id``，可正可负（supergroup 形如 -100xxxxxxxxxx，
  超出 32 位整型范围），所以这里用 ``BigInteger``。
- ``peer_kind`` 为字符串枚举（``private`` / ``group`` / ``supergroup`` / ``channel``），
  仅作展示用途，业务逻辑只看 ``peer_id``。
- ``peer_label`` 为加入忽略名单时的群名/用户名快照；后续群名变更不会自动同步。
- ``UniqueConstraint(account_id, peer_id)`` 保证同一账号下不会重复加入同一 peer。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# peer_kind 取值枚举（仅用于展示）
PEER_KIND_PRIVATE = "private"
PEER_KIND_GROUP = "group"
PEER_KIND_SUPERGROUP = "supergroup"
PEER_KIND_CHANNEL = "channel"

PEER_KINDS = (PEER_KIND_PRIVATE, PEER_KIND_GROUP, PEER_KIND_SUPERGROUP, PEER_KIND_CHANNEL)


class IgnoredPeer(Base):
    """[账号 × peer] 忽略名单的一行。

    详见模块 docstring。
    """

    __tablename__ = "ignored_peer"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Telethon chat_id：私聊 = 用户 id（正数）；群/超级群/频道 = 负数
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    peer_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # 群名/用户名快照；为空表示加入时未拿到（如 worker 离线场景的手填 ID）
    peer_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("account_id", "peer_id", name="uq_ignored_peer_account_peer"),
    )
