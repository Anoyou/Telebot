"""兼容层使用统计的持久化与读取（第 22 轮）。

- 计数本身在 :mod:`app.compat_usage`（进程内存、只依赖标准库）；
- :func:`enable_persistence` 在主进程 lifespan 与每个账号 worker 启动时调用：之后有命中时最多每
  :data:`FLUSH_DELAY_SECONDS` 秒把增量批量 upsert 到 ``plugin_compat_usage`` 表，进程退出时再写一次；
- upsert 用 ``INSERT … ON CONFLICT DO UPDATE``（SQLite ≥ 3.24 与 PostgreSQL 都支持），
  ``count = count + 增量``、``last_at`` 取较大值，多进程并发写也不会丢计数；其它数据库退回“先查后改”；
- 写库失败时增量放回内存，下次再写；统计失败永远不影响插件与兼容层本身。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import compat_usage
from ..db.models.plugin_compat_usage import PluginCompatUsage

log = logging.getLogger(__name__)

FLUSH_DELAY_SECONDS = 60.0

_SessionFactory = Callable[[], Any]
_state: dict[str, Any] = {"factory": None, "task": None}


def _session_factory() -> _SessionFactory:
    factory = _state.get("factory")
    if factory is not None:
        return factory
    from ..db.base import AsyncSessionLocal

    return AsyncSessionLocal


def enable_persistence(session_factory: _SessionFactory | None = None) -> None:
    """开启定期写库（幂等）。``session_factory`` 缺省用全局 ``AsyncSessionLocal``。"""

    if session_factory is not None:
        _state["factory"] = session_factory
    compat_usage.set_on_hit(_schedule_flush)
    if compat_usage.pending_snapshot():
        _schedule_flush()


def disable_persistence() -> None:
    compat_usage.set_on_hit(None)
    task = _state.get("task")
    if task is not None and not task.done():
        task.cancel()
    _state["task"] = None
    _state["factory"] = None


def _schedule_flush() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 没有事件循环（导入期 / 同步上下文）：留在内存，下一次命中或退出时再写
    task = _state.get("task")
    if task is not None and not task.done() and task.get_loop() is loop:
        return
    _state["task"] = loop.create_task(_delayed_flush())


async def _delayed_flush() -> None:
    try:
        await asyncio.sleep(FLUSH_DELAY_SECONDS)
        await flush()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 统计失败不能影响插件
        log.debug("兼容层使用统计写库失败", exc_info=True)


async def _upsert(db: AsyncSession, rows: dict[tuple[str, str], list[Any]]) -> None:
    table = PluginCompatUsage.__table__
    dialect = db.get_bind().dialect.name
    if dialect in {"postgresql", "sqlite"}:
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        for (compat_id, plugin_key), (count, first_at, last_at) in rows.items():
            stmt = dialect_insert(table).values(
                compat_id=compat_id,
                plugin_key=plugin_key,
                count=int(count),
                first_at=float(first_at),
                last_at=float(last_at),
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[table.c.compat_id, table.c.plugin_key],
                set_={
                    "count": table.c.count + excluded.count,
                    "first_at": case((excluded.first_at < table.c.first_at, excluded.first_at), else_=table.c.first_at),
                    "last_at": case((excluded.last_at > table.c.last_at, excluded.last_at), else_=table.c.last_at),
                },
            )
            await db.execute(stmt)
        return
    for (compat_id, plugin_key), (count, first_at, last_at) in rows.items():  # pragma: no cover - 其它数据库兜底
        current = await db.get(PluginCompatUsage, (compat_id, plugin_key))
        if current is None:
            db.add(
                PluginCompatUsage(
                    compat_id=compat_id, plugin_key=plugin_key, count=int(count), first_at=first_at, last_at=last_at
                )
            )
        else:
            current.count = int(current.count or 0) + int(count)
            current.first_at = min(float(current.first_at), float(first_at))
            current.last_at = max(float(current.last_at), float(last_at))


async def flush(session_factory: _SessionFactory | None = None) -> int:
    """把本进程待写增量写入数据库，返回写入的行数；失败时放回内存并抛出异常。"""

    rows = compat_usage.drain()
    if not rows:
        return 0
    factory = session_factory or _session_factory()
    try:
        async with factory() as db:
            await _upsert(db, rows)
            await db.commit()
    except BaseException:  # 含取消：增量放回内存，下次再写
        compat_usage.requeue(rows)
        raise
    return len(rows)


async def flush_quietly(session_factory: _SessionFactory | None = None) -> int:
    try:
        return await flush(session_factory)
    except Exception:  # noqa: BLE001
        log.debug("兼容层使用统计写库失败", exc_info=True)
        return 0


async def read_usage(db: AsyncSession) -> list[dict[str, Any]]:
    """按兼容层清单顺序返回每一项的总次数、插件数与按插件的明细（次数降序）。"""

    result = await db.execute(select(PluginCompatUsage))
    by_item: dict[str, list[dict[str, Any]]] = {}
    for row in result.scalars().all():
        by_item.setdefault(row.compat_id, []).append(
            {
                "plugin_key": row.plugin_key,
                "count": int(row.count or 0),
                "first_at": float(row.first_at) if row.first_at is not None else None,
                "last_at": float(row.last_at) if row.last_at is not None else None,
            }
        )
    items: list[dict[str, Any]] = []
    for meta in compat_usage.COMPAT_ITEMS:
        plugins = sorted(by_item.get(meta.id, []), key=lambda item: (-item["count"], item["plugin_key"]))
        last_values = [item["last_at"] for item in plugins if item["last_at"] is not None]
        first_values = [item["first_at"] for item in plugins if item["first_at"] is not None]
        items.append(
            {
                **meta.to_dict(),
                "total": sum(item["count"] for item in plugins),
                "plugin_count": len(plugins),
                "first_at": min(first_values) if first_values else None,
                "last_at": max(last_values) if last_values else None,
                "plugins": plugins,
            }
        )
    return items


__all__ = [
    "FLUSH_DELAY_SECONDS",
    "disable_persistence",
    "enable_persistence",
    "flush",
    "flush_quietly",
    "read_usage",
]
