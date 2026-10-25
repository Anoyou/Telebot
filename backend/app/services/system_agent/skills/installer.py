"""按安装来源拉取并校验 skill；API 与 System Agent 工具共用。

网络/拉取失败直接抛错，不自动重试（安装与更新都是管理员写操作）。
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ....db.models.agent_skill import (
    SKILL_SOURCE_GITHUB,
    SKILL_SOURCE_MD_UPLOAD,
    SKILL_SOURCE_PASTE,
    SKILL_SOURCE_PLUGIN_BUNDLED,
    SKILL_SOURCE_ZIP_UPLOAD,
    SKILL_SOURCE_ZIP_URL,
    SKILL_SOURCES,
    AgentSkill,
)
from .package import SkillValidationError
from .sources import (
    FetchedSkill,
    SkillSourceError,
    fetch_github,
    fetch_paste,
    fetch_zip_upload,
    fetch_zip_url,
)
from .store import builtin_names


async def _git_env(db: AsyncSession, page_url: str) -> dict[str, str] | None:
    """私有仓库：沿用插件仓库里为同一 GitHub 仓库保存的凭据。"""

    try:
        from ...plugin_repo_service import git_env_for_source_url
    except Exception:  # noqa: BLE001
        return None
    for candidate in (page_url, page_url + ".git", page_url.rstrip("/") + "/"):
        try:
            env = await git_env_for_source_url(db, candidate)
        except Exception:  # noqa: BLE001
            env = None
        if env:
            return env
    return None


async def fetch_source(
    db: AsyncSession,
    *,
    source: str,
    url: str | None = None,
    ref: str | None = None,
    path: str | None = None,
    zip_base64: str | None = None,
    skill_md: str | None = None,
) -> FetchedSkill:
    kind = str(source or "").strip().lower()
    if kind not in SKILL_SOURCES:
        raise SkillValidationError(
            "BAD_SOURCE", "安装来源只能是 github、zip_url、zip_upload、md_upload 或 paste。"
        )
    reserved = builtin_names()
    if kind == SKILL_SOURCE_GITHUB:
        if not str(url or "").strip():
            raise SkillValidationError("MISSING_URL", "请填写 GitHub 链接。")
        from .sources import parse_github_url

        location = parse_github_url(str(url))
        return await fetch_github(
            str(url),
            ref=ref,
            path=path,
            git_env=await _git_env(db, location.page_url),
            reserved_names=reserved,
        )
    if kind == SKILL_SOURCE_ZIP_URL:
        if not str(url or "").strip():
            raise SkillValidationError("MISSING_URL", "请填写 zip 下载链接。")
        return await fetch_zip_url(str(url), path=path, reserved_names=reserved)
    if kind == SKILL_SOURCE_ZIP_UPLOAD:
        if not str(zip_base64 or "").strip():
            raise SkillValidationError("MISSING_ARCHIVE", "请选择要上传的 zip 文件。")
        return fetch_zip_upload(str(zip_base64), path=path, reserved_names=reserved)
    if not str(skill_md or "").strip():
        raise SkillValidationError(
            "EMPTY_SKILL_MD",
            "请选择 SKILL.md 文件。" if kind == SKILL_SOURCE_MD_UPLOAD else "请粘贴 SKILL.md 内容。",
        )
    fetched = fetch_paste(str(skill_md), reserved_names=reserved)
    return dc_replace(fetched, source_type=kind)


async def fetch_update(
    db: AsyncSession,
    row: AgentSkill,
    *,
    zip_base64: str | None = None,
    skill_md: str | None = None,
) -> FetchedSkill:
    """更新：远程来源按原地址/原分支重新拉取；上传与粘贴来源需提供新内容。"""

    if row.source_type == SKILL_SOURCE_PLUGIN_BUNDLED:
        raise SkillValidationError(
            "BUNDLED_UPDATES_WITH_PLUGIN",
            "插件内置的 skill 随插件升级自动更新；要修改内容请使用“编辑”，或“恢复默认”。",
        )
    if row.source_type == SKILL_SOURCE_GITHUB:
        return await fetch_source(
            db,
            source=SKILL_SOURCE_GITHUB,
            url=row.source_url,
            # 记录的是分支名时重新拉取最新提交；当初按提交 SHA 安装的保持该提交。
            ref=row.source_ref,
            path=row.source_path,
        )
    if row.source_type == SKILL_SOURCE_ZIP_URL:
        return await fetch_source(db, source=SKILL_SOURCE_ZIP_URL, url=row.source_url, path=row.source_path)
    if zip_base64:
        return await fetch_source(db, source=SKILL_SOURCE_ZIP_UPLOAD, zip_base64=zip_base64, path=row.source_path)
    if skill_md:
        kind = row.source_type if row.source_type in {SKILL_SOURCE_PASTE, SKILL_SOURCE_MD_UPLOAD} else SKILL_SOURCE_PASTE
        return await fetch_source(db, source=kind, skill_md=skill_md)
    raise SkillValidationError(
        "UPDATE_NEEDS_CONTENT",
        "这个 skill 来自上传或粘贴，没有可重新拉取的地址；请上传新的 zip 或粘贴新的 SKILL.md。",
    )


def error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "code": getattr(exc, "code", "SKILL_ERROR"),
        "message": getattr(exc, "message", None) or str(exc),
    }


__all__ = ["SkillSourceError", "error_payload", "fetch_source", "fetch_update"]
