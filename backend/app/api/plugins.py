"""插件管理 REST API（内置插件账号矩阵）。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.feature import BUILTIN_FEATURES, AccountFeature, Feature
from ..deps import CurrentUser, DBSession
from ..redis_client import get_redis
from ..schemas.plugin_runtime import PluginCapabilitiesResponse, PluginHookStatsResponse
from ..services import audit, feature_service, platform_capabilities
from ..worker.ipc import CMD_RELOAD_PLUGIN, cmd_channel, make_cmd
from ..worker.plugins import plugin_stats
from ..worker.plugins.capabilities import capability_catalog
from ..worker.plugins.deprecations import DEPRECATION_INDEX, deprecation_catalog
from ..worker.plugins.versioning import PLUGIN_API_VERSION, PLUGIN_CONTRACT_VERSION

log = logging.getLogger(__name__)
router = APIRouter(tags=["plugins"])


class PluginInstallRequest(BaseModel):
    plugin_key: str
    account_ids: list[int]


class PluginAccountActionRequest(BaseModel):
    account_ids: list[int]


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _is_builtin(plugin_key: str) -> bool:
    # BUILTIN_FEATURES 是惰性字典，首次访问时自动扫描；此后可通过 refresh() 强制刷新。
    # 这里不主动 refresh，避免每次 API 请求都扫文件系统；seed_builtin_features 负责刷新。
    return plugin_key in BUILTIN_FEATURES


@router.get("/api/plugins/installed")
async def list_installed(
    db: DBSession,
    _user: CurrentUser,
    account_id: int | None = None,
) -> list[dict[str, Any]]:
    q = select(AccountFeature, Feature).join(Feature, AccountFeature.feature_key == Feature.key)
    if account_id is not None:
        q = q.where(AccountFeature.account_id == account_id)
    rows = (await db.execute(q)).all()
    out: list[dict[str, Any]] = []
    for af, feat in rows:
        out.append(
            {
                "account_id": af.account_id,
                "feature_key": af.feature_key,
                "display_name": feat.display_name,
                "is_builtin": feat.is_builtin,
                "enabled": af.enabled,
                "state": af.state,
                "last_error": af.last_error,
                "version": feat.version,
            }
        )
    return out


@router.get("/api/plugins/capabilities", response_model=PluginCapabilitiesResponse)
async def get_plugin_capabilities(_user: CurrentUser) -> PluginCapabilitiesResponse:
    """宿主能力注册表、Plugin API 版本、插件稳定契约版本（0.115.0+）、弃用登记、ctx.host.read 资源（含模型清单 ai.models@1）与中间件限制。"""

    from .. import __version__
    from ..services.render_profile import core_middleware_catalog
    from ..worker.plugins.contract import CONTRACT_BASELINE_PLUGIN_API
    from ..worker.plugins.host_read import host_resource_catalog
    from ..worker.plugins.middleware import limits_catalog

    return PluginCapabilitiesResponse(
        host_version=str(__version__),
        plugin_api_version=PLUGIN_API_VERSION,
        contract_version=PLUGIN_CONTRACT_VERSION,
        contract_baseline_plugin_api=CONTRACT_BASELINE_PLUGIN_API,
        capabilities=capability_catalog(),
        deprecations=deprecation_catalog(),
        host_resources=host_resource_catalog(),
        core_middleware=core_middleware_catalog(),
        middleware_limits=limits_catalog(),
    )


def _validated_plugin_key(key: str) -> str:
    text = str(key or "").strip()
    if not text or len(text) > 96 or not all(ch.isalnum() or ch in "._-" for ch in text):
        raise _bad("INVALID_PLUGIN_KEY", "插件 key 不合法")
    return text


@router.get("/api/plugins/{key}/stats", response_model=PluginHookStatsResponse)
async def get_plugin_hook_stats(
    key: str,
    _user: CurrentUser,
    account_id: int | None = None,
) -> PluginHookStatsResponse:
    """插件每个钩子 / 事件处理的调用数、耗时与失败数，以及已弃用接口的调用次数。

    数据由各账号 worker 每几秒批量写入 Redis，保留到最近一次调用后 7 天。
    """

    plugin_key = _validated_plugin_key(key)
    try:
        await plugin_stats.get_recorder().flush(get_redis())
    except Exception:  # noqa: BLE001 - 主进程本地增量写入失败不影响读取
        log.debug("flush local plugin stats failed", exc_info=True)
    try:
        data = await plugin_stats.read_plugin_stats(get_redis(), plugin_key, account_id=account_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("读取插件钩子统计失败 plugin=%s: %s", plugin_key, type(exc).__name__)
        return PluginHookStatsResponse(
            plugin_key=plugin_key,
            account_id=account_id,
            available=False,
            message="统计存储暂时不可用（Redis 无法连接），稍后再试。",
        )
    deprecations = []
    for item in data["deprecations"]:
        meta = DEPRECATION_INDEX.get(item["id"])
        deprecations.append(
            {
                **item,
                "summary": meta.summary if meta else None,
                "replacement": meta.replacement if meta else None,
                "removal": meta.removal if meta else None,
            }
        )
    return PluginHookStatsResponse(
        plugin_key=plugin_key,
        account_id=account_id,
        hooks=data["hooks"],
        totals=data["totals"],
        deprecations=deprecations,
        retention_days=plugin_stats.STATS_TTL_SECONDS // 86400,
    )


@router.delete("/api/plugins/{key}/stats", status_code=204)
async def reset_plugin_hook_stats(key: str, _user: CurrentUser) -> None:
    """清零插件的钩子统计与弃用计数（各 worker 进程内尚未写入的增量会在下次写入时重新出现）。"""

    plugin_key = _validated_plugin_key(key)
    try:
        await plugin_stats.reset_plugin_stats(get_redis(), plugin_key)
    except Exception as exc:  # noqa: BLE001
        raise _bad("STATS_UNAVAILABLE", "统计存储暂时不可用（Redis 无法连接），稍后再试。", 503) from exc


def _ensure_builtin_or_501(plugin_key: str) -> None:
    if not _is_builtin(plugin_key):
        raise HTTPException(
            status_code=501,
            detail={
                "code": "NOT_IMPLEMENTED",
                "message": f"该旧入口仅支持内置插件: {plugin_key}",
            },
        )


@router.post("/api/plugins/install")
async def install_plugin(
    payload: PluginInstallRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    _ensure_builtin_or_501(payload.plugin_key)
    await feature_service.seed_builtin_features(db)
    await _ensure_accounts_exist(db, payload.account_ids)
    try:
        n = await feature_service.bulk_set_enabled(
            db,
            payload.account_ids,
            payload.plugin_key,
            enabled=True,
            triggered_by_user_id=user.id,
        )
    except platform_capabilities.PluginCapabilityBlocked as exc:
        raise _bad(exc.error_code, str(exc), 409) from exc
    await audit.write(
        db,
        user.id,
        "plugin.install",
        target=f"plugin:{payload.plugin_key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/uninstall")
async def uninstall_plugin(
    payload: PluginInstallRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    _ensure_builtin_or_501(payload.plugin_key)
    await _ensure_accounts_exist(db, payload.account_ids)
    n = await feature_service.bulk_set_enabled(
        db,
        payload.account_ids,
        payload.plugin_key,
        enabled=False,
        triggered_by_user_id=user.id,
    )
    await audit.write(
        db,
        user.id,
        "plugin.uninstall",
        target=f"plugin:{payload.plugin_key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/{key}/enable")
async def enable_plugin(
    key: str, payload: PluginAccountActionRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    _ensure_builtin_or_501(key)
    await feature_service.seed_builtin_features(db)
    await _ensure_accounts_exist(db, payload.account_ids)
    try:
        n = await feature_service.bulk_set_enabled(
            db,
            payload.account_ids,
            key,
            enabled=True,
            triggered_by_user_id=user.id,
        )
    except platform_capabilities.PluginCapabilityBlocked as exc:
        raise _bad(exc.error_code, str(exc), 409) from exc
    await audit.write(
        db,
        user.id,
        "plugin.enable",
        target=f"plugin:{key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/{key}/disable")
async def disable_plugin(
    key: str, payload: PluginAccountActionRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    _ensure_builtin_or_501(key)
    await _ensure_accounts_exist(db, payload.account_ids)
    n = await feature_service.bulk_set_enabled(db, payload.account_ids, key, enabled=False)
    await audit.write(
        db,
        user.id,
        "plugin.disable",
        target=f"plugin:{key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/{key}/reload")
async def reload_plugin_endpoint(
    key: str, payload: PluginAccountActionRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    _ensure_builtin_or_501(key)
    await _ensure_accounts_exist(db, payload.account_ids)
    redis = None
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        redis = None
    n = 0
    for aid in payload.account_ids:
        if redis is not None:
            try:
                await redis.publish(
                    cmd_channel(aid),
                    make_cmd(CMD_RELOAD_PLUGIN, plugin_key=key),
                )
                n += 1
            except Exception:  # noqa: BLE001
                log.debug("reload plugin 广播失败 aid=%s", aid, exc_info=True)
    await audit.write(
        db,
        user.id,
        "plugin.reload",
        target=f"plugin:{key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"sent": n}


async def _ensure_accounts_exist(db, aids: list[int]) -> None:
    for aid in aids:
        if await db.get(Account, aid) is None:
            raise _bad("ACCOUNT_NOT_FOUND", f"账号不存在: {aid}", 404)


__all__ = ["router"]
