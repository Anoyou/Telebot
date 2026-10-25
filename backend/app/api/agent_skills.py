"""Agent Skills 管理 API：列出 / 详情 / 安装 / 更新 / 启停 / 卸载。

安装、更新、启停、卸载都是管理员写操作，写入审计日志；服务端不自动重试拉取。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser, DBSession
from ..schemas.agent_skill import (
    AgentSkillContentIn,
    AgentSkillDetailOut,
    AgentSkillEnablePatch,
    AgentSkillFileOut,
    AgentSkillInstallIn,
    AgentSkillListOut,
    AgentSkillOut,
    AgentSkillUninstallOut,
    AgentSkillUpdateIn,
    AgentSkillUpdateOut,
    AgentSkillUpstreamOut,
)
from ..services.system_agent.skills import BUILTIN_SKILLS
from ..services.system_agent.skills import package as skill_package
from ..services.system_agent.skills import store as skill_store
from ..services.system_agent.skills.installer import error_payload, fetch_source, fetch_update
from ..services.system_agent.skills.package import SkillValidationError, truncate_text
from ..services.system_agent.skills.sources import SkillSourceError

router = APIRouter(prefix="/api/agent-skills", tags=["agent-skills"])

_DETAIL_MAX_CHARS = 262_144


def _raise(exc: Exception) -> None:
    if isinstance(exc, skill_store.SkillNotFound):
        raise HTTPException(404, detail=error_payload(exc)) from exc
    if isinstance(exc, skill_store.SkillConflict):
        raise HTTPException(409, detail=error_payload(exc)) from exc
    if isinstance(exc, SkillSourceError):
        raise HTTPException(502 if exc.code.startswith(("DOWNLOAD", "GIT")) else 400, detail=error_payload(exc)) from exc
    if isinstance(exc, SkillValidationError):
        raise HTTPException(400, detail=error_payload(exc)) from exc
    raise exc


@router.get("", response_model=AgentSkillListOut)
async def list_agent_skills(db: DBSession, _user: CurrentUser):
    """列出已安装 skills 与内置领域技能（含启用范围与来源）。"""

    return {
        "items": await skill_store.list_all_views(db),
        "limits": {
            "max_files": skill_package.MAX_SKILL_FILES,
            "max_file_bytes": skill_package.MAX_FILE_BYTES,
            "max_skill_bytes": skill_package.MAX_SKILL_TOTAL_BYTES,
            "max_archive_bytes": skill_package.MAX_ARCHIVE_BYTES,
            "agent_load_max_chars": skill_store.AGENT_LOAD_MAX_CHARS,
            "plugin_load_max_chars": skill_store.PLUGIN_LOAD_MAX_CHARS,
        },
    }


@router.get("/{name}", response_model=AgentSkillDetailOut)
async def get_agent_skill(name: str, db: DBSession, _user: CurrentUser):
    """skill 详情：SKILL.md 原文与文件清单。"""

    builtin = next((skill for skill in BUILTIN_SKILLS if skill.name == name), None)
    if builtin is not None:
        disabled = await skill_store.disabled_builtin_names(db)
        view = skill_store.builtin_view(builtin.name, builtin.description, enabled=name not in disabled)
        return {**view, "skill_md": builtin.render_prompt(), "files": []}
    try:
        row = await skill_store.get_installed(db, name)
    except skill_store.SkillNotFound as exc:
        _raise(exc)
    files = await skill_store.list_files(db, row.id)
    entry = next((item for item in files if item.path == skill_package.SKILL_ENTRY), None)
    return {
        **skill_store.skill_view(row),
        "skill_md": truncate_text(entry.content if entry else "", _DETAIL_MAX_CHARS)[0],
        "files": [{"path": item.path, "size": int(item.size or 0)} for item in files],
    }


@router.get("/{name}/files/{path:path}", response_model=AgentSkillFileOut)
async def get_agent_skill_file(name: str, path: str, db: DBSession, _user: CurrentUser):
    """读取 skill 内的一个文本文件（只读展示，不执行）。"""

    try:
        row = await skill_store.get_installed(db, name)
        normalized = skill_package.normalize_skill_path(path)
    except (skill_store.SkillNotFound, SkillValidationError) as exc:
        raise HTTPException(404, detail=error_payload(exc)) from exc
    files = await skill_store.list_files(db, row.id)
    item = next((entry for entry in files if entry.path == normalized), None)
    if item is None:
        raise HTTPException(404, detail={"code": "FILE_NOT_FOUND", "message": f"文件不存在：{normalized}"})
    content, truncated = truncate_text(item.content, _DETAIL_MAX_CHARS)
    return {"name": row.name, "path": item.path, "content": content, "truncated": truncated}


@router.post("", response_model=AgentSkillOut, status_code=201)
async def install_agent_skill(body: AgentSkillInstallIn, db: DBSession, user: CurrentUser):
    """从 GitHub、zip（上传或链接）或粘贴的 SKILL.md 安装 skill。"""

    try:
        fetched = await fetch_source(
            db,
            source=body.source,
            url=body.url,
            ref=body.ref,
            path=body.path,
            zip_base64=body.zip_base64,
            skill_md=body.skill_md,
        )
        row = await skill_store.install(
            db,
            fetched,
            user_id=getattr(user, "id", None),
            enabled_agent=body.enabled_agent,
            enabled_plugins=body.enabled_plugins,
        )
        view = skill_store.skill_view(row)
        await db.commit()
    except (skill_store.SkillConflict, SkillSourceError, SkillValidationError) as exc:
        await db.rollback()
        _raise(exc)
    return view


@router.post("/{name}/update", response_model=AgentSkillUpdateOut)
async def update_agent_skill(name: str, body: AgentSkillUpdateIn, db: DBSession, user: CurrentUser):
    """GitHub / zip 链接来源按原地址重新拉取；上传或粘贴来源需提供新内容；插件内置 skill 随插件升级。"""

    try:
        row = await skill_store.get_installed(db, name)
        fetched = await fetch_update(db, row, zip_base64=body.zip_base64, skill_md=body.skill_md)
        changed = await skill_store.replace(db, row, fetched, user_id=getattr(user, "id", None))
        view = skill_store.skill_view(row)
        await db.commit()
    except (skill_store.SkillNotFound, skill_store.SkillConflict, SkillSourceError, SkillValidationError) as exc:
        await db.rollback()
        _raise(exc)
    return {"skill": view, "changed": changed}


@router.patch("/{name}", response_model=AgentSkillOut)
async def patch_agent_skill(name: str, body: AgentSkillEnablePatch, db: DBSession, user: CurrentUser):
    """分别启用/停用系统 Agent 与插件使用范围；内置技能只能切换系统 Agent。"""

    try:
        view = await skill_store.set_enabled(
            db,
            name,
            enabled_agent=body.enabled_agent,
            enabled_plugins=body.enabled_plugins,
            user_id=getattr(user, "id", None),
        )
        await db.commit()
    except (skill_store.SkillNotFound, skill_store.SkillConflict, SkillValidationError) as exc:
        await db.rollback()
        _raise(exc)
    return view


@router.delete("/{name}", response_model=AgentSkillUninstallOut)
async def uninstall_agent_skill(name: str, db: DBSession, user: CurrentUser):
    """卸载 skill，并从所有插件配置的 skill 选择中移除；内置技能不可卸载。"""

    try:
        result = await skill_store.uninstall(db, name, user_id=getattr(user, "id", None))
        await db.commit()
    except (skill_store.SkillNotFound, skill_store.SkillConflict) as exc:
        await db.rollback()
        _raise(exc)
    return result


@router.put("/{name}/content", response_model=AgentSkillOut)
async def edit_agent_skill(name: str, body: AgentSkillContentIn, db: DBSession, user: CurrentUser):
    """编辑 SKILL.md。插件内置 skill 的修改保存为覆盖层，插件升级不会覆盖。"""

    try:
        row = await skill_store.edit_content(db, name, body.skill_md, user_id=getattr(user, "id", None))
        view = skill_store.skill_view(row)
        await db.commit()
    except (skill_store.SkillNotFound, skill_store.SkillConflict, SkillValidationError) as exc:
        await db.rollback()
        _raise(exc)
    return view


@router.get("/{name}/upstream", response_model=AgentSkillUpstreamOut)
async def get_agent_skill_upstream(name: str, db: DBSession, _user: CurrentUser):
    """插件内置 skill：当前版本与插件分发的默认版本及差异。"""

    try:
        return await skill_store.upstream_info(db, name)
    except (skill_store.SkillNotFound, skill_store.SkillConflict) as exc:
        _raise(exc)


@router.post("/{name}/restore-default", response_model=AgentSkillOut)
async def restore_agent_skill_default(name: str, db: DBSession, user: CurrentUser):
    """插件内置 skill：放弃修改，恢复为插件分发的默认版本。"""

    try:
        row = await skill_store.restore_default(db, name, user_id=getattr(user, "id", None))
        view = skill_store.skill_view(row)
        await db.commit()
    except (skill_store.SkillNotFound, skill_store.SkillConflict, SkillValidationError) as exc:
        await db.rollback()
        _raise(exc)
    return view


@router.post("/{name}/keep-modified", response_model=AgentSkillOut)
async def keep_agent_skill_modified(name: str, db: DBSession, user: CurrentUser):
    """插件内置 skill：保留自己的修改，不再提示这一版“内置有新版本”。"""

    try:
        row = await skill_store.acknowledge_upstream(db, name, user_id=getattr(user, "id", None))
        view = skill_store.skill_view(row)
        await db.commit()
    except (skill_store.SkillNotFound, skill_store.SkillConflict) as exc:
        await db.rollback()
        _raise(exc)
    return view
