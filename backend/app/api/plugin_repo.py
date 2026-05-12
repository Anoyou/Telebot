"""插件仓库 API 路由：保存仓库 + 浏览仓库内插件 + 选择性安装。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import DBSession
from ..schemas.plugin_repo import PluginRepoCreate, PluginRepoOut, PluginRepoPlugin
from ..schemas.remote_plugin import RemotePluginOut
from ..services import plugin_repo_service as svc
from ..services.plugin_repo_service import (
    DuplicatePluginRepo,
    PluginNotInRepo,
    PluginRepoError,
    PluginRepoNotFound,
)
from ..services.remote_plugin_service import (
    DuplicatePluginName,
    GitOperationFailed,
    InvalidPluginMetadata,
    InvalidSourceUrl,
    RemotePluginError,
    trigger_reload,
)

router = APIRouter(prefix="/api/plugin-repos", tags=["plugin-repos"])


@router.get("", response_model=list[PluginRepoOut])
async def list_plugin_repos(db: DBSession):
    """列出所有保存的插件仓库。"""
    return await svc.list_repos(db)


@router.post("", response_model=PluginRepoOut, status_code=201)
async def create_plugin_repo(body: PluginRepoCreate, db: DBSession):
    """保存一个新仓库（仅写库；浏览插件请单独调 ``/{id}/plugins``）。"""
    try:
        row = await svc.create_repo(
            db, body.url, name=body.name, description=body.description,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except DuplicatePluginRepo as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message}) from e
    except InvalidSourceUrl as e:
        raise HTTPException(400, detail={"code": e.code, "message": e.message}) from e
    except PluginRepoError as e:
        raise HTTPException(400, detail={"code": e.code, "message": e.message}) from e
    except RemotePluginError as e:
        raise HTTPException(400, detail={"code": e.code, "message": e.message}) from e


@router.delete("/{repo_id}")
async def delete_plugin_repo(repo_id: int, db: DBSession):
    """删除仓库（**不**联动卸载已安装的插件，只是从“目录索引”里摘掉）。"""
    found = await svc.delete_repo(db, repo_id)
    if not found:
        raise HTTPException(
            404,
            detail={"code": "REPO_NOT_FOUND", "message": f"仓库不存在: id={repo_id}"},
        )
    await db.commit()
    return {"ok": True, "id": repo_id}


@router.get("/{repo_id}/plugins", response_model=list[PluginRepoPlugin])
async def list_repo_plugins(repo_id: int, db: DBSession):
    """列出指定仓库内所有可装插件。

    首次访问会触发 ``git clone``；后续访问只做 ``git fetch + reset``，比较快。
    任一插件目录的 plugin.json 损坏会被静默跳过（已记 warning）。
    """
    try:
        return await svc.list_plugins_in_repo(db, repo_id)
    except PluginRepoNotFound as e:
        raise HTTPException(404, detail={"code": e.code, "message": e.message}) from e
    except (GitOperationFailed, InvalidPluginMetadata) as e:
        raise HTTPException(400, detail={"code": e.code, "message": e.message}) from e
    except (PluginRepoError, RemotePluginError) as e:
        raise HTTPException(400, detail={"code": e.code, "message": e.message}) from e


class InstallFromRepoBody(BaseModel):
    """``POST /{id}/plugins/{name}/install`` 的可选 body。"""

    default_enabled: bool = False


@router.post(
    "/{repo_id}/plugins/{plugin_name}/install",
    response_model=RemotePluginOut,
    status_code=201,
)
async def install_plugin_from_repo(
    repo_id: int,
    plugin_name: str,
    db: DBSession,
    body: InstallFromRepoBody | None = None,
):
    """安装仓库中指定名字的插件。

    安装完成后插件全局默认禁用，要让某账号生效需在“账号插件管理”里勾选；
    若想一次性给所有账号启用，传 ``default_enabled=true``。
    """
    default_enabled = bool(body.default_enabled) if body else False
    try:
        row = await svc.install_plugin_from_repo(
            db, repo_id, plugin_name, default_enabled=default_enabled,
        )
        await db.commit()
        await db.refresh(row)
        await trigger_reload(db, row.name)
        return row
    except PluginRepoNotFound as e:
        raise HTTPException(404, detail={"code": e.code, "message": e.message}) from e
    except PluginNotInRepo as e:
        raise HTTPException(404, detail={"code": e.code, "message": e.message}) from e
    except DuplicatePluginName as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message}) from e
    except (GitOperationFailed, InvalidPluginMetadata) as e:
        raise HTTPException(400, detail={"code": e.code, "message": e.message}) from e
    except (PluginRepoError, RemotePluginError) as e:
        raise HTTPException(400, detail={"code": e.code, "message": e.message}) from e
