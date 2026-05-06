"""设备伪装库 API。

提供：
  - GET    /api/device-profiles            列表
  - POST   /api/device-profiles            新建（is_default=true 时自动把其它行置 false）
  - GET    /api/device-profiles/{pid}      详情
  - PATCH  /api/device-profiles/{pid}      修改
  - POST   /api/device-profiles/{pid}/default   置为系统默认
  - DELETE /api/device-profiles/{pid}      删除（被账号引用时仅置 SET NULL）

修改 device profile 的字段不会影响**已有 session**：Telegram 把设备名绑在 auth_key 上，
账号要让 TG 端显示新设备名必须重新登录。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from ..db.models.account import Account, DeviceProfile
from ..deps import CurrentUser, DBSession
from ..schemas.device_profile import (
    DeviceProfileCreate,
    DeviceProfileOut,
    DeviceProfileUpdate,
)
from ..services import audit

router = APIRouter(prefix="/api/device-profiles", tags=["device-profiles"])


def _err(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


async def _clear_other_defaults(db: DBSession, keep_id: int | None = None) -> None:
    """把除 keep_id 之外的所有 profile is_default 置 false。"""
    stmt = update(DeviceProfile).values(is_default=False)
    if keep_id is not None:
        stmt = stmt.where(DeviceProfile.id != keep_id)
    await db.execute(stmt)


# ── CRUD ─────────────────────────────────────────────────────────
@router.get("", response_model=list[DeviceProfileOut])
async def list_profiles(db: DBSession, _user: CurrentUser) -> list[DeviceProfile]:
    rows = (
        await db.execute(select(DeviceProfile).order_by(DeviceProfile.id))
    ).scalars().all()
    return list(rows)


@router.post(
    "", response_model=DeviceProfileOut, status_code=status.HTTP_201_CREATED
)
async def create_profile(
    payload: DeviceProfileCreate, db: DBSession, user: CurrentUser
) -> DeviceProfile:
    if payload.is_default:
        await _clear_other_defaults(db)
    p = DeviceProfile(
        name=payload.name,
        device_model=payload.device_model,
        system_version=payload.system_version,
        app_version=payload.app_version,
        lang_code=payload.lang_code,
        system_lang_code=payload.system_lang_code,
        is_default=payload.is_default,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await audit.write(
        db,
        user.id,
        "create_device_profile",
        target=str(p.id),
        detail={"name": p.name, "is_default": p.is_default},
    )
    await db.commit()
    return p


@router.get("/{pid}", response_model=DeviceProfileOut)
async def get_profile(
    pid: int, db: DBSession, _user: CurrentUser
) -> DeviceProfile:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)
    return p


@router.patch("/{pid}", response_model=DeviceProfileOut)
async def patch_profile(
    pid: int,
    payload: DeviceProfileUpdate,
    db: DBSession,
    user: CurrentUser,
) -> DeviceProfile:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)

    # 字段更新
    for field in (
        "name",
        "device_model",
        "system_version",
        "app_version",
        "lang_code",
        "system_lang_code",
    ):
        v = getattr(payload, field)
        if v is not None:
            setattr(p, field, v)

    # 默认状态切换
    if payload.is_default is True and not p.is_default:
        await _clear_other_defaults(db, keep_id=pid)
        p.is_default = True
    elif payload.is_default is False and p.is_default:
        # 不允许直接取消唯一的默认。如果用户想换默认，应该 POST .../{other}/default
        raise _err(
            "CANNOT_UNSET_DEFAULT",
            "不能直接取消唯一的默认 profile；请先把其它 profile 设为默认",
        )

    await db.commit()
    await audit.write(db, user.id, "update_device_profile", target=str(pid))
    await db.commit()
    await db.refresh(p)
    return p


@router.post("/{pid}/default", response_model=DeviceProfileOut)
async def set_default(
    pid: int, db: DBSession, user: CurrentUser
) -> DeviceProfile:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)
    await _clear_other_defaults(db, keep_id=pid)
    p.is_default = True
    await db.commit()
    await audit.write(
        db, user.id, "set_default_device_profile", target=str(pid)
    )
    await db.commit()
    await db.refresh(p)
    return p


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(pid: int, db: DBSession, user: CurrentUser) -> None:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)
    if p.is_default:
        # 删掉默认会导致没有 fallback；要求用户先把别的设为默认
        raise _err(
            "CANNOT_DELETE_DEFAULT",
            "默认 profile 不能删除；请先把其它 profile 设为默认",
        )
    # 被账号引用：FK 是 SET NULL，可以删，但提醒用户哪些账号会受影响（仅警告，不阻止）
    used = (
        await db.execute(
            select(Account.id).where(Account.device_profile_id == pid)
        )
    ).scalars().all()
    await db.delete(p)
    await db.commit()
    await audit.write(
        db,
        user.id,
        "delete_device_profile",
        target=str(pid),
        detail={"affected_accounts": list(used)},
    )
    await db.commit()
