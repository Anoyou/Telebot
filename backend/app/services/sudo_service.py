"""Sudo 用户管理服务。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import SudoUser
from ..schemas.sudo import SudoUserCreate, SudoUserUpdate
from ..util.sudo_permissions import build_sudo_chat_scope, build_sudo_command_scope


def _duplicate_error(tg_user_id: int) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "SUDO_ALREADY_EXISTS", "message": f"该 Telegram 用户已是 sudo 用户: {tg_user_id}"},
    )


async def get_sudo_users(db: AsyncSession, account_id: int | None = None) -> list[SudoUser]:
    """获取 Sudo 用户列表。"""
    stmt = select(SudoUser)
    if account_id:
        stmt = stmt.where(SudoUser.account_id == account_id)
    stmt = stmt.order_by(SudoUser.id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_sudo_user(db: AsyncSession, sudo_id: int) -> SudoUser | None:
    """获取单个 Sudo 用户。"""
    stmt = select(SudoUser).where(SudoUser.id == sudo_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_sudo_user(
    db: AsyncSession,
    data: SudoUserCreate,
    *,
    commit: bool = True,
) -> SudoUser:
    """创建 Sudo 用户。

    ``sudo_user`` 上有 (account_id, tg_user_id) 唯一索引：先查重给可读的 409；
    并发窗口再由 IntegrityError 兜底，同样返回结构化 409 而不是裸 500。
    """
    dup = (
        await db.execute(
            select(SudoUser.id).where(
                SudoUser.account_id == data.account_id,
                SudoUser.tg_user_id == data.tg_user_id,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise _duplicate_error(data.tg_user_id)
    sudo_user = SudoUser(
        account_id=data.account_id,
        tg_user_id=data.tg_user_id,
        display_name=data.display_name,
        allowed_chat_ids=build_sudo_chat_scope(
            data.allowed_chat_ids,
            allow_all=data.allow_all_chats,
        ),
        allowed_commands=build_sudo_command_scope(
            data.allowed_commands,
            allow_all=data.allow_all_commands,
        ),
    )
    db.add(sudo_user)
    try:
        if commit:
            await db.commit()
        else:
            await db.flush()
    except IntegrityError as exc:
        # 并发插入撞唯一索引：回滚本事务，保持与前置查重一致的 409 语义。
        await db.rollback()
        raise _duplicate_error(data.tg_user_id) from exc
    await db.refresh(sudo_user)
    return sudo_user


async def update_sudo_user(
    db: AsyncSession,
    sudo_id: int,
    data: SudoUserUpdate,
    *,
    commit: bool = True,
) -> SudoUser | None:
    """更新 Sudo 用户。"""
    sudo_user = await get_sudo_user(db, sudo_id)
    if not sudo_user:
        return None

    if data.display_name is not None:
        sudo_user.display_name = data.display_name
    chat_scope_touched = (
        "allowed_chat_ids" in data.model_fields_set
        or "allow_all_chats" in data.model_fields_set
    )
    if chat_scope_touched:
        sudo_user.allowed_chat_ids = build_sudo_chat_scope(
            data.allowed_chat_ids,
            allow_all=bool(data.allow_all_chats),
        )

    command_scope_touched = (
        "allowed_commands" in data.model_fields_set
        or "allow_all_commands" in data.model_fields_set
    )
    if command_scope_touched:
        sudo_user.allowed_commands = build_sudo_command_scope(
            data.allowed_commands,
            allow_all=bool(data.allow_all_commands),
        )

    if commit:
        await db.commit()
    else:
        await db.flush()
    await db.refresh(sudo_user)
    return sudo_user


async def delete_sudo_user(
    db: AsyncSession,
    sudo_id: int,
    *,
    commit: bool = True,
) -> bool:
    """删除 Sudo 用户。"""
    sudo_user = await get_sudo_user(db, sudo_id)
    if not sudo_user:
        return False

    await db.delete(sudo_user)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return True
