"""插件市场 API（0.113.0）：浏览 / 搜索 / 安装 / 并发更新 / 来源管理 / 固定版本 / 健康窗口回滚。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser, DBSession
from ..schemas.plugin_market import (
    PluginMarketCatalog,
    PluginMarketInstallBody,
    PluginMarketPinBody,
    PluginMarketPinResult,
    PluginMarketRollout,
    PluginMarketSettings,
    PluginMarketSettingsPatch,
    PluginMarketSource,
    PluginMarketSourceCreate,
    PluginMarketSourcePatch,
    PluginMarketUpdateBody,
    PluginMarketUpdateResult,
)
from ..schemas.remote_plugin import RemotePluginOut
from ..services import plugin_market_service as svc
from ..services.platform_capabilities import PluginCapabilityBlocked
from ..services.plugin_repo_service import PluginNotInRepo, PluginRepoError
from ..services.remote_plugin_service import DuplicatePluginName, RemotePluginError, trigger_reload

router = APIRouter(prefix="/api/plugin-market", tags=["plugin-market"])


def _http(exc: svc.MarketError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


@router.get("/catalog", response_model=PluginMarketCatalog)
async def get_catalog(db: DBSession, _user: CurrentUser, refresh: bool = False):
    """所有已启用来源的插件目录（含已安装版本、更新角标、兼容性、固定版本与健康窗口状态）。"""

    return await svc.build_catalog(db, refresh=refresh)


@router.post("/install", response_model=RemotePluginOut, status_code=201)
async def install_plugin(body: PluginMarketInstallBody, db: DBSession, user: CurrentUser):
    """从市场安装插件（与“插件仓库”安装同一流程：克隆、校验 plugin.json、写库、通知重载）。"""

    try:
        view = await svc.install(
            db,
            source_id=body.source_id,
            key=body.key,
            default_enabled=body.default_enabled,
            triggered_by_user_id=user.id,
        )
        await db.commit()
        await trigger_reload(db, view.name)
        return view
    except svc.MarketError as exc:
        raise _http(exc) from exc
    except PluginNotInRepo as exc:
        raise HTTPException(404, detail={"code": exc.code, "message": exc.message}) from exc
    except DuplicatePluginName as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": exc.message}) from exc
    except PluginCapabilityBlocked as exc:
        raise HTTPException(409, detail={"code": exc.error_code, "message": str(exc)}) from exc
    except (PluginRepoError, RemotePluginError) as exc:
        raise HTTPException(400, detail={"code": exc.code, "message": exc.message}) from exc


@router.post("/update", response_model=PluginMarketUpdateResult)
async def update_plugins(body: PluginMarketUpdateBody, db: DBSession, user: CurrentUser):
    """更新指定插件，或（不传 keys）一键更新全部有新版本且未固定的插件；并行执行，结果逐个返回。"""

    run = await svc.update_from_market(db, keys=body.keys, triggered_by_user_id=user.id)
    return run.summary()


@router.get("/sources", response_model=list[PluginMarketSource])
async def list_sources(db: DBSession, _user: CurrentUser):
    return await svc.list_sources(db)


@router.post("/sources", response_model=PluginMarketSource, status_code=201)
async def add_source(body: PluginMarketSourceCreate, db: DBSession, _user: CurrentUser):
    try:
        item = await svc.add_source(db, url=body.url, name=body.name, trust=body.trust)
        await db.commit()
        return item
    except svc.MarketError as exc:
        raise _http(exc) from exc


@router.patch("/sources/{source_id}", response_model=PluginMarketSource)
async def update_source(source_id: str, body: PluginMarketSourcePatch, db: DBSession, _user: CurrentUser):
    try:
        item = await svc.update_source(db, source_id, body.model_dump(exclude_unset=True))
        await db.commit()
        return item
    except svc.MarketError as exc:
        raise _http(exc) from exc


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str, db: DBSession, _user: CurrentUser):
    try:
        await svc.delete_source(db, source_id)
        await db.commit()
        return {"ok": True, "id": source_id}
    except svc.MarketError as exc:
        raise _http(exc) from exc


@router.get("/settings", response_model=PluginMarketSettings)
async def get_settings(db: DBSession, _user: CurrentUser):
    return await svc.load_settings(db)


@router.put("/settings", response_model=PluginMarketSettings)
async def put_settings(body: PluginMarketSettingsPatch, db: DBSession, _user: CurrentUser):
    result = await svc.save_settings(db, body.model_dump(exclude_unset=True))
    await db.commit()
    return result


@router.put("/pins/{key}", response_model=PluginMarketPinResult)
async def set_pin(key: str, body: PluginMarketPinBody, db: DBSession, _user: CurrentUser):
    """固定 / 取消固定插件版本：固定后“全部更新”和自动更新都会跳过它。"""

    try:
        version = await svc.set_pinned(db, key, pinned=body.pinned)
        await db.commit()
        return {"key": key, "pinned_version": version}
    except svc.MarketError as exc:
        raise _http(exc) from exc


@router.get("/rollouts", response_model=list[PluginMarketRollout])
async def list_rollouts(db: DBSession, _user: CurrentUser):
    rollouts = await svc.load_rollouts(db)
    return [svc._rollout_public(item) for item in rollouts.values()]


@router.post("/rollouts/{key}/rollback", response_model=PluginMarketRollout)
async def rollback_now(key: str, _user: CurrentUser):
    """健康观察期内立即回滚到更新前的版本。"""

    try:
        return await svc.rollback(key, reason="管理员手动回滚", automatic=False)
    except svc.MarketError as exc:
        raise _http(exc) from exc


@router.post("/rollouts/{key}/accept", response_model=PluginMarketRollout)
async def accept_now(key: str, _user: CurrentUser):
    """提前确认新版本正常：结束观察期并删除保留的旧版目录。"""

    try:
        return await svc.accept_rollout(key)
    except svc.MarketError as exc:
        raise _http(exc) from exc
