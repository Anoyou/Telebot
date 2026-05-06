"""设备伪装 profile 解析。

worker 启动 / 登录向导构造 TelegramClient 时调用，按以下顺序回退：
  1. 账号显式绑定的 profile（``Account.device_profile_id``）
  2. 表里 ``is_default = true`` 的 profile
  3. 都没有 → ``HARDCODED_FALLBACK``

返回一个 dataclass 形态的结果，方便给 TelegramClient 解包。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account, DeviceProfile


@dataclass(frozen=True)
class ResolvedDeviceProfile:
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str

    def telethon_kwargs(self) -> dict[str, str]:
        return {
            "device_model": self.device_model,
            "system_version": self.system_version,
            "app_version": self.app_version,
            "lang_code": self.lang_code,
            "system_lang_code": self.system_lang_code,
        }


# 极端情况下的兜底（数据库为空、迁移未跑）。Telegram 设备列表里看到的"客户端"。
HARDCODED_FALLBACK = ResolvedDeviceProfile(
    device_model="MacBook Pro",
    system_version="macOS 14.5",
    app_version="Telegram macOS 11.5",
    lang_code="zh",
    system_lang_code="zh-Hans",
)


def _from_row(row: DeviceProfile) -> ResolvedDeviceProfile:
    return ResolvedDeviceProfile(
        device_model=row.device_model,
        system_version=row.system_version,
        app_version=row.app_version,
        lang_code=row.lang_code,
        system_lang_code=row.system_lang_code,
    )


async def get_default(db: AsyncSession) -> ResolvedDeviceProfile:
    """读 ``is_default = true`` 的 profile；找不到回退到 HARDCODED_FALLBACK。"""
    row = (
        await db.execute(
            select(DeviceProfile).where(DeviceProfile.is_default.is_(True)).limit(1)
        )
    ).scalar_one_or_none()
    return _from_row(row) if row else HARDCODED_FALLBACK


async def get_by_id(db: AsyncSession, profile_id: int) -> ResolvedDeviceProfile | None:
    """按 id 读；找不到返 None（让调用方决定继续回退到 default 还是报错）。"""
    row = await db.get(DeviceProfile, profile_id)
    return _from_row(row) if row else None


async def resolve_for_account(
    db: AsyncSession, account: Account
) -> ResolvedDeviceProfile:
    """根据账号绑定关系决定用哪条 profile。worker 启动时调这个。"""
    if account.device_profile_id is not None:
        bound = await get_by_id(db, account.device_profile_id)
        if bound is not None:
            return bound
    return await get_default(db)
