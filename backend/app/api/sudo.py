"""Sudo 用户管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser, DBSession
from ..redis_client import get_redis
from ..schemas.sudo import SudoUserCreate, SudoUserResponse, SudoUserUpdate
from ..services.sudo_service import (
    create_sudo_user,
    delete_sudo_user,
    get_sudo_user,
    get_sudo_users,
    update_sudo_user,
)
from ..worker.ipc import CMD_RELOAD_CONFIG, publish_cmd_with_ack

router = APIRouter(prefix="/api/sudo", tags=["sudo"])


async def _notify_sudo_reload(account_id: int | None) -> None:
    if account_id is None:
        return
    try:
        redis = get_redis()
        await publish_cmd_with_ack(redis, int(account_id), CMD_RELOAD_CONFIG)
    except Exception:
        return


@router.get("", response_model=list[SudoUserResponse])
async def list_sudo_users(
    account_id: int | None = None,
    db: DBSession = None,
    user: CurrentUser = None,
) -> list[SudoUserResponse]:
    """获取 Sudo 用户列表。"""
    users = await get_sudo_users(db, account_id)
    return [SudoUserResponse.model_validate(u) for u in users]


@router.get("/{sudo_id}", response_model=SudoUserResponse)
async def get_sudo_user_detail(
    sudo_id: int,
    db: DBSession = None,
    user: CurrentUser = None,
) -> SudoUserResponse:
    """获取 Sudo 用户详情。"""
    sudo_user = await get_sudo_user(db, sudo_id)
    if not sudo_user:
        raise HTTPException(status_code=404, detail="Sudo user not found")
    return SudoUserResponse.model_validate(sudo_user)


@router.post("", response_model=SudoUserResponse, status_code=201)
async def create_sudo_user_endpoint(
    data: SudoUserCreate,
    db: DBSession = None,
    user: CurrentUser = None,
) -> SudoUserResponse:
    """创建 Sudo 用户。"""
    sudo_user = await create_sudo_user(db, data)
    await _notify_sudo_reload(sudo_user.account_id)
    return SudoUserResponse.model_validate(sudo_user)


@router.patch("/{sudo_id}", response_model=SudoUserResponse)
async def update_sudo_user_endpoint(
    sudo_id: int,
    data: SudoUserUpdate,
    db: DBSession = None,
    user: CurrentUser = None,
) -> SudoUserResponse:
    """更新 Sudo 用户。"""
    sudo_user = await update_sudo_user(db, sudo_id, data)
    if not sudo_user:
        raise HTTPException(status_code=404, detail="Sudo user not found")
    await _notify_sudo_reload(sudo_user.account_id)
    return SudoUserResponse.model_validate(sudo_user)


@router.delete("/{sudo_id}", status_code=204)
async def delete_sudo_user_endpoint(
    sudo_id: int,
    db: DBSession = None,
    user: CurrentUser = None,
) -> None:
    """删除 Sudo 用户。"""
    sudo_user = await get_sudo_user(db, sudo_id)
    account_id = sudo_user.account_id if sudo_user is not None else None
    success = await delete_sudo_user(db, sudo_id)
    if not success:
        raise HTTPException(status_code=404, detail="Sudo user not found")
    await _notify_sudo_reload(account_id)
