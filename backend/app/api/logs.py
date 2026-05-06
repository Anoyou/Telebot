"""日志查询 API（PRD §9.6）。

涵盖：
  - ``GET /api/logs/audit``：操作日志（Web 端 Action）
  - ``GET /api/logs/runtime``：运行日志（worker 输出，由 supervisor 批量消费 stream 落库）

只读接口，鉴权后返回最近一段时间的日志列表，按 ts 倒序。前端在 Dashboard
摘要卡 + 日志页过滤都使用本路由。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..db.models.log import AuditLog, RuntimeLog
from ..deps import CurrentUser, DBSession

router = APIRouter(tags=["logs"])


# ── 出参 ─────────────────────────────────────────────────────────
class AuditLogItem(BaseModel):
    """审计（操作）日志条目。"""

    id: int
    ts: datetime
    user_id: int | None
    action: str
    target: str | None
    detail: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class RuntimeLogItem(BaseModel):
    """运行日志条目（worker 上抛）。"""

    id: int
    ts: datetime
    # 兼容字段：前端 E 已使用 ``created_at``，这里同步输出，避免破坏现有页面
    created_at: datetime
    account_id: int | None
    level: str
    source: str | None
    message: str
    detail: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: RuntimeLog) -> RuntimeLogItem:
        return cls(
            id=row.id,
            ts=row.ts,
            created_at=row.ts,
            account_id=row.account_id,
            level=row.level,
            source=row.source,
            message=row.message,
            detail=row.detail,
        )

    model_config = ConfigDict(from_attributes=True)


# ── /api/logs/audit ──────────────────────────────────────────────
@router.get("/api/logs/audit", response_model=list[AuditLogItem])
async def list_audit_logs(
    db: DBSession,
    _user: CurrentUser,
    user_id: int | None = Query(None, description="按 web_user 过滤"),
    since: datetime | None = Query(None, description="ISO 时间，仅返回此后的日志"),
    limit: int = Query(50, ge=1, le=500),
) -> list[AuditLogItem]:
    """返回最近的操作日志，按时间倒序。"""
    stmt = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if since is not None:
        stmt = stmt.where(AuditLog.ts >= since)
    rows = (await db.execute(stmt)).scalars().all()
    return [AuditLogItem.model_validate(r) for r in rows]


# ── /api/logs/runtime ────────────────────────────────────────────
# source 别名映射：
#   - 历史数据 source="worker" / "plugin" 一直存在，新代码改写成 "system" / "event"
#   - 前端只暴露 "system" / "event" 两种 tab；这里把请求转换成对应集合
_SOURCE_ALIAS: dict[str, tuple[str, ...]] = {
    "system": ("system", "worker"),
    "event": ("event", "plugin"),
}


@router.get("/api/logs/runtime", response_model=list[RuntimeLogItem])
async def list_runtime_logs(
    db: DBSession,
    _user: CurrentUser,
    account_id: int | None = Query(None, description="按账号过滤"),
    level: str | None = Query(None, description="debug | info | warn | warning | error"),
    source: str | None = Query(
        None,
        description='日志类别："system"（worker 启停/错误）或 "event"（消息事件/plugin 命中）',
    ),
    since: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[RuntimeLogItem]:
    """返回最近运行日志。

    兼容前端传 ``level=warning``：内部映射为 ``level >= 'warn'``（warn + error）。
    ``source`` 支持 ``"system"`` / ``"event"`` 两种 tab，自动包含历史 ``worker`` / ``plugin`` 旧值。
    """
    stmt = select(RuntimeLog).order_by(RuntimeLog.ts.desc()).limit(limit)
    if account_id is not None:
        stmt = stmt.where(RuntimeLog.account_id == account_id)
    if since is not None:
        stmt = stmt.where(RuntimeLog.ts >= since)
    if level:
        norm = level.lower()
        if norm == "warning":
            stmt = stmt.where(RuntimeLog.level.in_(("warn", "warning", "error")))
        else:
            stmt = stmt.where(RuntimeLog.level == norm)
    if source:
        aliases = _SOURCE_ALIAS.get(source.lower())
        if aliases is not None:
            stmt = stmt.where(RuntimeLog.source.in_(aliases))
        else:
            stmt = stmt.where(RuntimeLog.source == source)
    rows = (await db.execute(stmt)).scalars().all()
    return [RuntimeLogItem.from_row(r) for r in rows]
