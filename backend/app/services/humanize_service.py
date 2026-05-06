"""拟人化（humanize）配置 Service 转发层。

实际数据访问已经在 ``rate_limit_service`` 里实现（``HumanizeConfig``
表跟风控引擎共享）。这个文件只是 Sprint 2 计划要求的稳定门面：
后续若把拟人化迁出风控模块，只需修改这里的转发，调用方无需改动。
"""

from __future__ import annotations

from datetime import time as dtime

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import HumanizeConfig
from . import rate_limit_service as _rate


async def get_humanize(db: AsyncSession, account_id: int) -> HumanizeConfig | None:
    """读取账号的拟人化配置；未配置返回 ``None``，由调用方决定如何回退默认值。"""
    return await _rate.get_humanize(db, account_id)


async def upsert_humanize(
    db: AsyncSession,
    account_id: int,
    *,
    jitter_pct: int | None = None,
    typing_simulate: bool | None = None,
    typing_min_ms: int | None = None,
    typing_max_ms: int | None = None,
    typing_probability: int | None = None,
    read_before_reply: bool | None = None,
    active_window_start: dtime | None = None,
    active_window_end: dtime | None = None,
    cold_start_days: int | None = None,
) -> HumanizeConfig:
    """局部更新（PATCH 语义）：``None`` 字段保持不变，其余落库。"""
    return await _rate.upsert_humanize(
        db,
        account_id,
        jitter_pct=jitter_pct,
        typing_simulate=typing_simulate,
        typing_min_ms=typing_min_ms,
        typing_max_ms=typing_max_ms,
        typing_probability=typing_probability,
        read_before_reply=read_before_reply,
        active_window_start=active_window_start,
        active_window_end=active_window_end,
        cold_start_days=cold_start_days,
    )


__all__ = ["get_humanize", "upsert_humanize"]
