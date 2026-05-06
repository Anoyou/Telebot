"""忽略 peer 名单的 Pydantic schema。

API：
- ``IgnoredPeerOut``       — 返回给前端的单行
- ``IgnoredPeerCreate``    — POST /api/accounts/{aid}/ignored-peers 入参
- ``RecentPeerItem``       — GET /api/accounts/{aid}/recent-peers 返回的每条
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..db.models.ignored_peer import PEER_KINDS


class IgnoredPeerOut(BaseModel):
    """忽略名单的一行（GET / POST 响应）。"""

    id: int
    account_id: int
    peer_id: int
    peer_kind: str
    peer_label: str | None = None
    added_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IgnoredPeerCreate(BaseModel):
    """加入忽略名单的入参。

    手填场景下 ``peer_kind`` 用户可能不知道——保留 "private" 默认值；后端不强校验，
    只把字符串原样落库（白名单见 ``PEER_KINDS``）。
    """

    peer_id: int
    peer_kind: str = Field(default="private")
    peer_label: str | None = None

    def normalized_kind(self) -> str:
        """归一化 ``peer_kind``：不在白名单内则视为 ``private``。"""
        return self.peer_kind if self.peer_kind in PEER_KINDS else "private"


class RecentPeerItem(BaseModel):
    """worker 内存里的最近活跃 peer 一条。

    ``ts`` 是 epoch 秒（``time.time()``），由 worker 写入；前端做相对时间显示。
    """

    peer_id: int
    peer_kind: str
    peer_label: str | None = None
    ts: float


class RecentPeersResponse(BaseModel):
    """``GET /recent-peers`` 的包裹响应。

    ``worker_alive`` 区分两种 ``items=[]`` 的情形：
    - ``True``  → worker 在跑，只是当前没有最近活跃 peer（让用户给自己发条消息试试）
    - ``False`` → worker 没在跑 / RPC 超时（让用户去概览暂停 → 启动一次）

    单独包裹一层是为了不破坏 ``RecentPeerItem`` 现有 schema。
    """

    worker_alive: bool
    items: list[RecentPeerItem]
