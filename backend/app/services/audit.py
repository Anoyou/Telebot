"""操作日志（audit log）写入工具。

由各 API 写操作调用，记录到 ``audit_log`` 表。本模块不在内部 commit，事务由调用方控制。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.log import AuditLog


async def write(
    db: AsyncSession,
    user_id: int | None,
    action: str,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """追加一条 audit log。

    :param db: 当前请求的 AsyncSession（事务由 API 层控制）。
    :param user_id: 触发操作的 Web 用户 id；后台/系统操作可为 None。
    :param action: 动作动词，例如 ``account.create`` / ``account.delete``。
    :param target: 目标资源的字符串描述（如 ``account:42``）。
    :param detail: 任意 JSON 附加信息（脱敏后再写入）。
    """
    db.add(AuditLog(user_id=user_id, action=action, target=target, detail=detail))
    # 不 commit；调用方负责事务边界
