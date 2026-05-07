"""Sudo 用户管理服务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import SudoUser
from ..schemas.sudo import SudoUserCreate, SudoUserUpdate


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


async def create_sudo_user(db: AsyncSession, data: SudoUserCreate) -> SudoUser:
    """创建 Sudo 用户。"""
    sudo_user = SudoUser(
        account_id=data.account_id,
        tg_user_id=data.tg_user_id,
        display_name=data.display_name,
        allowed_chat_ids=data.allowed_chat_ids,
        allowed_commands=data.allowed_commands,
    )
    db.add(sudo_user)
    await db.commit()
    await db.refresh(sudo_user)
    return sudo_user


async def update_sudo_user(
    db: AsyncSession, sudo_id: int, data: SudoUserUpdate
) -> SudoUser | None:
    """更新 Sudo 用户。"""
    sudo_user = await get_sudo_user(db, sudo_id)
    if not sudo_user:
        return None

    if data.display_name is not None:
        sudo_user.display_name = data.display_name
    if data.allowed_chat_ids is not None:
        sudo_user.allowed_chat_ids = data.allowed_chat_ids
    if data.allowed_commands is not None:
        sudo_user.allowed_commands = data.allowed_commands

    await db.commit()
    await db.refresh(sudo_user)
    return sudo_user


async def delete_sudo_user(db: AsyncSession, sudo_id: int) -> bool:
    """删除 Sudo 用户。"""
    sudo_user = await get_sudo_user(db, sudo_id)
    if not sudo_user:
        return False

    await db.delete(sudo_user)
    await db.commit()
    return True
