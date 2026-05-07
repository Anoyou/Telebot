"""命令别名管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser, DBSession
from ..schemas.alias import CommandAliasCreate, CommandAliasResponse, CommandAliasUpdate
from ..services.alias_service import (
    create_alias,
    delete_alias,
    get_alias,
    get_aliases,
    update_alias,
)

router = APIRouter(prefix="/api/aliases", tags=["aliases"])


@router.get("", response_model=list[CommandAliasResponse])
async def list_aliases(
    account_id: int | None = None,
    db: DBSession = None,
    user: CurrentUser = None,
) -> list[CommandAliasResponse]:
    """获取命令别名列表。"""
    aliases = await get_aliases(db, account_id)
    return [CommandAliasResponse.model_validate(a) for a in aliases]


@router.get("/{alias_id}", response_model=CommandAliasResponse)
async def get_alias_detail(
    alias_id: int,
    db: DBSession = None,
    user: CurrentUser = None,
) -> CommandAliasResponse:
    """获取命令别名详情。"""
    alias = await get_alias(db, alias_id)
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    return CommandAliasResponse.model_validate(alias)


@router.post("", response_model=CommandAliasResponse, status_code=201)
async def create_alias_endpoint(
    data: CommandAliasCreate,
    db: DBSession = None,
    user: CurrentUser = None,
) -> CommandAliasResponse:
    """创建命令别名。"""
    alias = await create_alias(db, data)
    return CommandAliasResponse.model_validate(alias)


@router.patch("/{alias_id}", response_model=CommandAliasResponse)
async def update_alias_endpoint(
    alias_id: int,
    data: CommandAliasUpdate,
    db: DBSession = None,
    user: CurrentUser = None,
) -> CommandAliasResponse:
    """更新命令别名。"""
    alias = await update_alias(db, alias_id, data)
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    return CommandAliasResponse.model_validate(alias)


@router.delete("/{alias_id}", status_code=204)
async def delete_alias_endpoint(
    alias_id: int,
    db: DBSession = None,
    user: CurrentUser = None,
) -> None:
    """删除命令别名。"""
    success = await delete_alias(db, alias_id)
    if not success:
        raise HTTPException(status_code=404, detail="Alias not found")
