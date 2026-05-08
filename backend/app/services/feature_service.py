"""功能（feature/plugin）业务层：feature 表 seed、account_feature 启停 + 矩阵查询。

API 层调本服务而不直接读写 ORM，便于以后引入更复杂的状态机或缓存。
所有需要"通知 worker 热重载"的写操作都会发一条 IPC ``CMD_RELOAD_CONFIG``，
异常吞掉避免影响 DB 事务结果。

热重载设计：
- ``seed_builtin_features`` 每次调用时强制刷新 ``BUILTIN_FEATURES``（动态扫描 builtin 目录），
  保证新增 builtin 插件目录后，主进程调用任意 API 都能立即把新行写入 ``feature`` 表。
- worker 端 ``reload_account_config`` 也会刷新注册表后重激活，两侧相互独立。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account
from ..db.models.feature import (
    BUILTIN_FEATURES,
    FEATURE_STATE_ACTIVE,
    FEATURE_STATE_DISABLED,
    AccountFeature,
    Feature,
)
from ..redis_client import get_redis
from ..worker.ipc import CMD_RELOAD_CONFIG, cmd_channel, make_cmd

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# Feature 表 seed
# ─────────────────────────────────────────────────────
async def seed_builtin_features(db: AsyncSession) -> int:
    """确保内置 feature 行存在；返回新增条数。

    每次调用都先强制刷新 ``BUILTIN_FEATURES`` 字典（重新扫描 builtin 目录），
    保证新增插件目录后不重启主进程也能被感知。

    幂等：已存在的行只校正 display_name / is_builtin，不会触发额外 INSERT；
    新行才增加计数并 commit。
    """
    # 强制刷新动态字典，确保扫描到最新文件系统状态
    BUILTIN_FEATURES.refresh()

    rows = (await db.execute(select(Feature))).scalars().all()
    existing: dict[str, Feature] = {f.key: f for f in rows}
    added = 0
    for key, name in BUILTIN_FEATURES.items():
        # 尝试从 manifest 读取 config_schema 和 version
        cfg_schema = None
        ver = None
        try:
            import importlib
            mod = importlib.import_module(f"app.worker.plugins.builtin.{key}.manifest")
            m = getattr(mod, "MANIFEST", None)
            if m is not None:
                cfg_schema = getattr(m, "config_schema", None)
                ver = getattr(m, "version", None)
        except Exception:  # noqa: BLE001
            pass

        if key in existing:
            f = existing[key]
            changed = False
            if f.display_name != name:
                f.display_name = name
                changed = True
            if not f.is_builtin:
                f.is_builtin = True
                changed = True
            if ver and f.version != ver:
                f.version = ver
                changed = True
            if cfg_schema:
                manifest = f.manifest or {}
                if manifest.get("config_schema") != cfg_schema:
                    manifest["config_schema"] = cfg_schema
                    f.manifest = manifest
                    changed = True
            if changed:
                await db.flush()
            continue
        manifest_data = {"config_schema": cfg_schema} if cfg_schema else None
        db.add(Feature(key=key, display_name=name, is_builtin=True, version=ver, manifest=manifest_data))
        added += 1
    if added:
        await db.commit()
    return added


# ─────────────────────────────────────────────────────
# 列表查询
# ─────────────────────────────────────────────────────
async def list_features(db: AsyncSession) -> list[Feature]:
    """列出所有已登记的 feature；首次调用时会自动 seed 内置行。"""
    await seed_builtin_features(db)
    rows = (
        await db.execute(select(Feature).order_by(Feature.is_builtin.desc(), Feature.key.asc()))
    ).scalars().all()
    return list(rows)


async def get_account_features(db: AsyncSession, aid: int) -> list[AccountFeature]:
    """返回某账号已登记的 [account_feature] 行（未启用过的不在结果里）。"""
    rows = (
        await db.execute(
            select(AccountFeature)
            .where(AccountFeature.account_id == aid)
            .order_by(AccountFeature.feature_key.asc())
        )
    ).scalars().all()
    return list(rows)


# ─────────────────────────────────────────────────────
# upsert：启停 feature + 修改 config
# ─────────────────────────────────────────────────────
async def set_account_feature(
    db: AsyncSession,
    aid: int,
    key: str,
    enabled: bool,
    config: dict[str, Any] | None = None,
    *,
    notify: bool = True,
) -> AccountFeature:
    """对 [账号 × feature] 做 upsert。

    - 若该 account_feature 不存在 → 新建；state 默认 ``disabled``。
    - 若 enabled 由 False→True：state 仍记为 disabled，等 worker 实际激活后再改成 active。
    - 若 enabled 由 True→False：直接把 state 改成 ``disabled``。
    - ``config`` 不为 None 时整体覆盖（便于前端"保存即覆盖"语义）。

    完成后视 ``notify`` 决定是否发 IPC 通知 worker reload。
    """
    af = (
        await db.execute(
            select(AccountFeature).where(
                AccountFeature.account_id == aid,
                AccountFeature.feature_key == key,
            )
        )
    ).scalar_one_or_none()
    if af is None:
        af = AccountFeature(
            account_id=aid,
            feature_key=key,
            enabled=enabled,
            config=dict(config or {}),
            state=FEATURE_STATE_DISABLED,
        )
        db.add(af)
    else:
        af.enabled = enabled
        if config is not None:
            af.config = dict(config)
        if not enabled:
            # 立刻把状态置 disabled；激活由 worker 反向写
            af.state = FEATURE_STATE_DISABLED
            af.last_error = None
    await db.commit()
    await db.refresh(af)
    if notify:
        await _notify_reload(aid)
    return af


# ─────────────────────────────────────────────────────
# 矩阵：行=账号、列=feature
# ─────────────────────────────────────────────────────
async def feature_matrix(db: AsyncSession) -> dict[str, Any]:
    """构造功能矩阵数据：

    返回结构::

        {
          "features": [{key, display_name, is_builtin, version}, ...],
          "accounts": [
            {"id": 1, "name": "...", "features": {"auto_reply": "active", "forward": "disabled", ...}}
          ]
        }
    """
    # 1) 保证内置 feature 行齐全
    features = await list_features(db)

    # 2) 拿全部账号 + 全部 account_feature
    accounts = (
        await db.execute(select(Account).order_by(Account.id.asc()))
    ).scalars().all()
    afs = (await db.execute(select(AccountFeature))).scalars().all()
    by_aid: dict[int, dict[str, str]] = {}
    for af in afs:
        # 默认按 state 显示；若 enabled=False 则强制 disabled，避免脏状态混淆
        if not af.enabled:
            cell = FEATURE_STATE_DISABLED
        else:
            cell = af.state or FEATURE_STATE_ACTIVE
        by_aid.setdefault(af.account_id, {})[af.feature_key] = cell

    rows: list[dict[str, Any]] = []
    for acc in accounts:
        cells: dict[str, str] = {}
        existing = by_aid.get(acc.id, {})
        for f in features:
            cells[f.key] = existing.get(f.key, FEATURE_STATE_DISABLED)
        rows.append(
            {
                "id": acc.id,
                "name": acc.display_name or acc.phone,
                "features": cells,
            }
        )

    return {
        "features": [
            {
                "key": f.key,
                "display_name": f.display_name,
                "is_builtin": f.is_builtin,
                "version": f.version,
                "config_schema": (f.manifest or {}).get("config_schema"),
            }
            for f in features
        ],
        "accounts": rows,
    }


# ─────────────────────────────────────────────────────
# 批量启停（plugins/install / uninstall 用）
# ─────────────────────────────────────────────────────
async def bulk_set_enabled(
    db: AsyncSession,
    aids: Iterable[int],
    key: str,
    enabled: bool,
) -> int:
    """对一组账号统一启 / 停某 feature。返回受影响条数。"""
    n = 0
    for aid in aids:
        await set_account_feature(db, aid, key, enabled, config=None, notify=True)
        n += 1
    return n


# ─────────────────────────────────────────────────────
# IPC：通知 worker reload
# ─────────────────────────────────────────────────────
async def _notify_reload(account_id: int) -> None:
    """对指定 worker 发 ``CMD_RELOAD_CONFIG``；redis 不可用时静默。"""
    try:
        redis = get_redis()
        await redis.publish(cmd_channel(account_id), make_cmd(CMD_RELOAD_CONFIG))
    except Exception:  # noqa: BLE001
        log.debug("通知 worker reload 失败 account=%s", account_id, exc_info=True)


__all__ = [
    "bulk_set_enabled",
    "feature_matrix",
    "get_account_features",
    "list_features",
    "seed_builtin_features",
    "set_account_feature",
]
