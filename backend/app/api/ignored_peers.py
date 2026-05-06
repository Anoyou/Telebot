"""忽略 peer 名单 REST API。

Endpoints（统一前缀 ``/api/accounts/{aid}/``）：
  - GET    /ignored-peers       列表
  - POST   /ignored-peers       加入；幂等
  - DELETE /ignored-peers/{id}  移除
  - GET    /recent-peers        最近活跃会话（worker 内存里的 LRU）

写操作完成后通过 IPC ``CMD_RELOAD_IGNORED`` 通知 worker 热更新。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import CurrentUser, DBSession
from ..schemas.ignored_peer import (
    IgnoredPeerCreate,
    IgnoredPeerOut,
    RecentPeerItem,
    RecentPeersResponse,
)
from ..services import audit, ignored_peer_service

router = APIRouter(tags=["ignored-peers"])


# ── 列表 ──────────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/ignored-peers",
    response_model=list[IgnoredPeerOut],
)
async def list_ignored(
    aid: int, db: DBSession, _user: CurrentUser
) -> list[IgnoredPeerOut]:
    """返回该账号的忽略 peer 列表（按 added_at 倒序）。"""
    rows = await ignored_peer_service.list_ignored(db, aid)
    return [IgnoredPeerOut.model_validate(r) for r in rows]


# ── 加入 ──────────────────────────────────────────────────────────
@router.post(
    "/api/accounts/{aid}/ignored-peers",
    response_model=IgnoredPeerOut,
    status_code=201,
)
async def add_ignored(
    aid: int,
    payload: IgnoredPeerCreate,
    db: DBSession,
    user: CurrentUser,
) -> IgnoredPeerOut:
    """加入忽略名单；同 (account_id, peer_id) 已存在直接返回原行（幂等）。

    成功后通过 IPC 通知 worker 热加载（worker 离线静默）。
    """
    row = await ignored_peer_service.add_ignored(db, aid, payload)
    await audit.write(
        db,
        user.id,
        "ignored_peer.add",
        target=f"account:{aid}/peer:{payload.peer_id}",
        detail={"peer_kind": row.peer_kind, "peer_label": row.peer_label},
    )
    await db.commit()
    # 提交事务后再下发 IPC，避免 worker 拉到尚未 commit 的视图
    await ignored_peer_service.notify_reload(aid)
    return IgnoredPeerOut.model_validate(row)


# ── 移除 ──────────────────────────────────────────────────────────
@router.delete("/api/accounts/{aid}/ignored-peers/{ignored_id}")
async def remove_ignored(
    aid: int,
    ignored_id: int,
    db: DBSession,
    user: CurrentUser,
) -> dict[str, bool]:
    """从忽略名单移除一行；找不到抛 404。"""
    await ignored_peer_service.remove_ignored(db, aid, ignored_id)
    await audit.write(
        db,
        user.id,
        "ignored_peer.remove",
        target=f"account:{aid}/ignored:{ignored_id}",
    )
    await db.commit()
    await ignored_peer_service.notify_reload(aid)
    return {"ok": True}


# ── 最近活跃 ──────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/recent-peers",
    response_model=RecentPeersResponse,
)
async def list_recent(
    aid: int, _db: DBSession, _user: CurrentUser
) -> RecentPeersResponse:
    """通过 IPC RPC 向对应 worker 拉一次"最近活跃 peer"列表（≤50 条）。

    超时 1.5s。返回包含 ``worker_alive`` 字段以便前端区分：
    - ``worker_alive=False`` → worker 没在跑（让用户去暂停 → 启动）
    - ``worker_alive=True``  且 ``items=[]`` → worker 在跑但没收到 incoming
    - ``worker_alive=True``  且 ``items=[...]`` → 正常
    """
    alive, raw = await ignored_peer_service.fetch_recent(aid)
    out: list[RecentPeerItem] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        peer_id = it.get("peer_id")
        ts = it.get("ts")
        if peer_id is None or ts is None:
            continue
        out.append(
            RecentPeerItem(
                peer_id=int(peer_id),
                peer_kind=str(it.get("peer_kind") or "private"),
                peer_label=it.get("peer_label"),
                ts=float(ts),
            )
        )
    return RecentPeersResponse(worker_alive=alive, items=out)


__all__ = ["router"]
