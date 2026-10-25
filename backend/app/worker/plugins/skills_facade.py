"""插件读取 Agent Skills 的安全 facade（``ctx.skills``）。

插件在 manifest ``permissions`` 声明 ``agent_skills`` 后注入。只暴露管理员已「对插件启用」的
已安装 skill，并且只返回文本：

- ``await ctx.skills.list(names=None)``：已授权 skill 的 name / description / version / 文件清单；
  传 ``names`` 时只返回其中仍可用的（插件通常传自己配置里多选的 skill 名）。
- ``await ctx.skills.load(name, max_chars=None)``：SKILL.md 正文（去掉 front matter），按上限截断。
- ``await ctx.skills.read_file(name, path, max_chars=None)``：skill 内一个附属文本文件。
- ``await ctx.skills.bundled(max_chars=None)``：本插件内置（``skills/<name>/SKILL.md`` 随插件分发）且
  仍对插件启用的 skill 正文，含管理员在 App/Web 的编辑覆盖层。插件加载时平台自动登记内置 skill：
  默认只对插件启用、不可卸载可停用；插件升级不覆盖用户修改，只标记“内置有新版本”。

skill 中的脚本从不执行。每次调用都实时读取数据库，管理员停用或卸载后立即生效。
内置领域技能描述的是 TelePilot 自身工具流程，不会提供给插件。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ...db.base import AsyncSessionLocal
from ...services.system_agent.skills import store as skill_store
from ...services.system_agent.skills.package import SkillValidationError, package_from_directory

log = logging.getLogger(__name__)
MAX_BUNDLED_SKILLS = 10
# 进程内已同步过的 (插件, 内容摘要)，避免每个账号加载插件都重复写库
_SYNCED: set[tuple[str, str]] = set()

# 插件侧（常面向访客）单次注入的硬上限；插件可以要求更小，不能更大。
PLUGIN_SKILL_MAX_CHARS = skill_store.PLUGIN_LOAD_MAX_CHARS * 2


class PluginSkillUnavailable(LookupError):
    """skill 不存在、未对插件启用或文件不存在。"""


class PluginSkills:
    def __init__(
        self,
        *,
        plugin_key: str,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._plugin_key = plugin_key
        self._session_factory = session_factory or AsyncSessionLocal

    @staticmethod
    def _limit(max_chars: int | None) -> int:
        if max_chars is None:
            return skill_store.PLUGIN_LOAD_MAX_CHARS
        try:
            value = int(max_chars)
        except (TypeError, ValueError):
            return skill_store.PLUGIN_LOAD_MAX_CHARS
        return max(200, min(value, PLUGIN_SKILL_MAX_CHARS))

    async def list(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        wanted = None if names is None else {str(item).strip() for item in names if str(item).strip()}
        if wanted is not None and not wanted:
            return []
        async with self._session_factory() as db:
            entries = await skill_store.catalog(db, skill_store.SCOPE_PLUGIN)
            result: list[dict[str, Any]] = []
            for entry in entries:
                if wanted is not None and entry.name not in wanted:
                    continue
                try:
                    row = await skill_store.get_installed(db, entry.name)
                except skill_store.SkillNotFound:
                    continue
                files = await skill_store.list_files(db, row.id)
                result.append(
                    {
                        "name": row.name,
                        "description": entry.description,
                        "version": row.version,
                        "files": [item.path for item in files if item.path != "SKILL.md"],
                    }
                )
            return result

    async def load(self, name: str, *, max_chars: int | None = None) -> dict[str, Any]:
        async with self._session_factory() as db:
            try:
                data = await skill_store.load_skill(
                    db, str(name or "").strip(), scope=skill_store.SCOPE_PLUGIN, max_chars=self._limit(max_chars)
                )
            except skill_store.SkillNotFound as exc:
                raise PluginSkillUnavailable(f"skill 不可用：{name}") from exc
        data["files"] = [item["path"] for item in data.get("files", [])]
        return data

    async def bundled(self, *, max_chars: int | None = None) -> list[dict[str, Any]]:
        async with self._session_factory() as db:
            items = await skill_store.bundled_for_plugin(db, self._plugin_key, max_chars=self._limit(max_chars))
        for item in items:
            item["files"] = [entry["path"] for entry in item.get("files", [])]
        return items

    async def read_file(self, name: str, path: str, *, max_chars: int | None = None) -> dict[str, Any]:
        async with self._session_factory() as db:
            try:
                return await skill_store.read_skill_file(
                    db,
                    str(name or "").strip(),
                    str(path or ""),
                    scope=skill_store.SCOPE_PLUGIN,
                    max_chars=self._limit(max_chars),
                )
            except skill_store.SkillNotFound as exc:
                raise PluginSkillUnavailable(f"skill 文件不可用：{name}/{path}") from exc


def read_bundled_packages(plugin_dir: Path | None) -> list[Any]:
    """读取插件目录 ``skills/*/SKILL.md``；不合规的跳过并记日志。"""

    if plugin_dir is None:
        return []
    root = plugin_dir / "skills"
    if not root.is_dir() or root.is_symlink():
        return []
    packages = []
    for child in sorted(root.iterdir())[: MAX_BUNDLED_SKILLS * 2]:
        if not child.is_dir() or child.is_symlink() or not (child / "SKILL.md").is_file():
            continue
        try:
            packages.append(package_from_directory(child, reserved_names=skill_store.builtin_names()))
        except SkillValidationError as exc:
            log.warning("插件内置 skill 校验失败 dir=%s: %s", child, exc.message)
        if len(packages) >= MAX_BUNDLED_SKILLS:
            break
    return packages


async def ensure_bundled_skills(
    plugin_key: str,
    plugin_dir: Path | None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    """插件加载时登记内置 skill（独立事务）；失败只记日志，不影响插件加载。"""

    packages = read_bundled_packages(plugin_dir)
    if not packages:
        return []
    digest = "|".join(f"{item.name}:{item.sha256}" for item in packages)
    if (plugin_key, digest) in _SYNCED:
        return []
    factory = session_factory or AsyncSessionLocal
    async with factory() as db:
        results = await skill_store.sync_bundled(db, plugin_key, packages)
        await db.commit()
    _SYNCED.add((plugin_key, digest))
    return results


__all__ = [
    "PLUGIN_SKILL_MAX_CHARS",
    "PluginSkillUnavailable",
    "PluginSkills",
    "ensure_bundled_skills",
    "read_bundled_packages",
]
