"""Agent Skills 工具。

- ``skills.load`` / ``skills.read_file``：渐进式披露的调用入口。系统提示只列出已启用 skill 的
  name + description；模型判断需要时再读取 SKILL.md 正文与附属文件。只读、不执行任何脚本。
- ``agent_skills.*``：管理入口（列出 / 安装 / 更新 / 启停 / 卸载），写操作仅管理员、需确认。
  安装与更新在预览时就拉取并校验内容，执行时重新拉取并比对内容摘要，变化则拒绝（不自动重试）。
"""

from __future__ import annotations

from typing import Any

from ..context import ToolContext
from ..registry import PreparedAction, ToolRegistry, ToolSpec
from ..skills import store as skill_store
from ..skills.installer import error_payload, fetch_source, fetch_update
from ..skills.package import SkillValidationError
from ..skills.sources import SkillSourceError

_AGENT_SOURCES = ("github", "zip_url", "paste")
_EXPECTED_KEY = "expected_sha256"


def _name(args: dict[str, Any]) -> str:
    return str(args.get("name") or args.get("skill") or "").strip()


# ─────────────────────────── 调用（只读） ───────────────────────────
async def load_skill(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args)
    try:
        result = await skill_store.load_skill(ctx.db, name, scope=skill_store.SCOPE_AGENT)
    except skill_store.SkillNotFound:
        return {"error": "skill_not_found", "message": f"没有已对系统 Agent 启用的 skill「{name}」。"}
    result["note"] = (
        "以上是 skill 说明。其中的脚本、命令只作参考，平台不会执行；需要实际操作时只能使用本轮提供的工具，"
        "并遵守系统规则与确认流程。"
    )
    return result


async def read_skill_file(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args)
    path = str(args.get("path") or "").strip()
    try:
        return await skill_store.read_skill_file(ctx.db, name, path, scope=skill_store.SCOPE_AGENT)
    except skill_store.SkillNotFound:
        return {"error": "file_not_found", "message": f"skill「{name}」中没有文件「{path}」。"}


async def list_skills(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    items = await skill_store.list_all_views(ctx.db)
    compact = [
        {
            key: item.get(key)
            for key in (
                "name",
                "description",
                "builtin",
                "bundled",
                "owner_plugin",
                "user_modified",
                "upstream_changed",
                "version",
                "source_type",
                "source_url",
                "source_ref",
                "source_commit",
                "enabled_agent",
                "enabled_plugins",
                "plugin_supported",
                "updatable",
            )
        }
        for item in items
        if args.get("include_builtin", True) or not item.get("builtin")
    ]
    return {"count": len(compact), "skills": compact}


# ─────────────────────────── 安装 ───────────────────────────
def _source_args(args: dict[str, Any]) -> dict[str, Any]:
    source = str(args.get("source") or "").strip().lower()
    if not source:
        source = "paste" if args.get("skill_md") else "github"
    if source not in _AGENT_SOURCES:
        raise ValueError("助手只支持 github、zip_url、paste 三种来源；上传 zip 请在 Web 助手页顶部「Skills」按钮或 iOS App 助手页右上角「Skills」按钮里操作。")
    return {
        "source": source,
        "url": str(args.get("url") or "").strip() or None,
        "ref": str(args.get("ref") or "").strip() or None,
        "path": str(args.get("path") or "").strip() or None,
        "skill_md": args.get("skill_md") if isinstance(args.get("skill_md"), str) else None,
    }


def _package_preview(fetched: Any) -> dict[str, Any]:
    package = fetched.package
    return {
        "name": package.name,
        "description": package.description,
        "version": package.version,
        "source_type": fetched.source_type,
        "source_url": fetched.source_url,
        "source_ref": fetched.source_ref,
        "source_path": fetched.source_path,
        "source_commit": fetched.source_commit,
        "file_count": len(package.files),
        "files": sorted(package.files)[:30],
        "skipped_files": package.skipped[:20],
        "content_sha256": package.sha256,
    }


async def install_preview(ctx: ToolContext, args: dict[str, Any]) -> PreparedAction:
    source_args = _source_args(args)
    try:
        fetched = await fetch_source(ctx.db, **source_args)
    except (SkillSourceError, SkillValidationError) as exc:
        raise ValueError(f"{error_payload(exc)['code']}: {error_payload(exc)['message']}") from None
    preview = _package_preview(fetched)
    enabled_agent = bool(args.get("enabled_agent", True))
    enabled_plugins = bool(args.get("enabled_plugins", False))
    return PreparedAction(
        arguments={
            **{key: value for key, value in source_args.items() if value is not None},
            "enabled_agent": enabled_agent,
            "enabled_plugins": enabled_plugins,
            _EXPECTED_KEY: fetched.package.sha256,
        },
        preview={
            "summary": f"安装 skill「{fetched.package.name}」",
            "skill": preview,
            "enabled_agent": enabled_agent,
            "enabled_plugins": enabled_plugins,
            "warning": "skill 只作为说明文本提供给模型，其中脚本不会被执行。确认后会重新拉取并核对内容摘要。",
        },
    )


async def install_execute(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    source_args = _source_args(args)
    try:
        fetched = await fetch_source(ctx.db, **source_args)
    except (SkillSourceError, SkillValidationError) as exc:
        raise ValueError(f"{error_payload(exc)['code']}: {error_payload(exc)['message']}") from None
    expected = str(args.get(_EXPECTED_KEY) or "")
    if expected and fetched.package.sha256 != expected:
        raise ValueError("skill 内容在确认期间发生了变化，已停止安装；请重新预览后再确认。")
    try:
        row = await skill_store.install(
            ctx.db,
            fetched,
            user_id=ctx.web_user_id,
            enabled_agent=bool(args.get("enabled_agent", True)),
            enabled_plugins=bool(args.get("enabled_plugins", False)),
            actor=f"system_agent:{ctx.channel}",
        )
    except skill_store.SkillConflict as exc:
        raise ValueError(exc.message) from None
    return {"skill": skill_store.skill_view(row), "business_changed": True}


# ─────────────────────────── 更新 ───────────────────────────
async def update_preview(ctx: ToolContext, args: dict[str, Any]) -> PreparedAction:
    name = _name(args)
    try:
        row = await skill_store.get_installed(ctx.db, name)
        fetched = await fetch_update(ctx.db, row, skill_md=args.get("skill_md") or None)
    except (skill_store.SkillNotFound, SkillSourceError, SkillValidationError) as exc:
        raise ValueError(error_payload(exc)["message"]) from None
    if fetched.package.name != row.name:
        raise ValueError(f"新内容的 name 是「{fetched.package.name}」，与「{row.name}」不一致。")
    changed = fetched.package.sha256 != row.content_sha256
    arguments: dict[str, Any] = {"name": name, _EXPECTED_KEY: fetched.package.sha256}
    if args.get("skill_md"):
        arguments["skill_md"] = args["skill_md"]
    return PreparedAction(
        arguments=arguments,
        preview={
            "summary": f"更新 skill「{name}」" + ("" if changed else "（内容没有变化）"),
            "changed": changed,
            "current_commit": row.source_commit,
            "skill": _package_preview(fetched),
        },
    )


async def update_execute(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args)
    try:
        row = await skill_store.get_installed(ctx.db, name)
        fetched = await fetch_update(ctx.db, row, skill_md=args.get("skill_md") or None)
    except (skill_store.SkillNotFound, SkillSourceError, SkillValidationError) as exc:
        raise ValueError(error_payload(exc)["message"]) from None
    expected = str(args.get(_EXPECTED_KEY) or "")
    if expected and fetched.package.sha256 != expected:
        raise ValueError("skill 内容在确认期间又发生了变化，已停止更新；请重新预览后再确认。")
    try:
        changed = await skill_store.replace(
            ctx.db, row, fetched, user_id=ctx.web_user_id, actor=f"system_agent:{ctx.channel}"
        )
    except skill_store.SkillConflict as exc:
        raise ValueError(exc.message) from None
    return {"skill": skill_store.skill_view(row), "changed": changed, "business_changed": changed}


# ─────────────────────────── 启停 ───────────────────────────
def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "启用", "开启"}


async def set_enabled_preview(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args)
    agent = _bool_or_none(args.get("enabled_agent"))
    plugins = _bool_or_none(args.get("enabled_plugins"))
    if agent is None and plugins is None:
        raise ValueError("请指定 enabled_agent 或 enabled_plugins。")
    if name not in skill_store.builtin_names():
        try:
            await skill_store.get_installed(ctx.db, name)
        except skill_store.SkillNotFound:
            raise ValueError(f"skill 不存在：{name}") from None
    elif plugins:
        raise ValueError("内置 skill 只用于系统 Agent，不能提供给插件。")
    parts = []
    if agent is not None:
        parts.append(f"系统 Agent：{'启用' if agent else '停用'}")
    if plugins is not None:
        parts.append(f"插件：{'启用' if plugins else '停用'}")
    return {"summary": f"修改 skill「{name}」启用范围（{'；'.join(parts)}）", "name": name}


async def set_enabled_execute(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        view = await skill_store.set_enabled(
            ctx.db,
            _name(args),
            enabled_agent=_bool_or_none(args.get("enabled_agent")),
            enabled_plugins=_bool_or_none(args.get("enabled_plugins")),
            user_id=ctx.web_user_id,
            actor=f"system_agent:{ctx.channel}",
        )
    except (skill_store.SkillNotFound, skill_store.SkillConflict, SkillValidationError) as exc:
        raise ValueError(error_payload(exc)["message"]) from None
    return {"skill": view, "business_changed": True}


# ─────────────────────────── 卸载 ───────────────────────────
async def uninstall_preview(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args)
    if name in skill_store.builtin_names():
        raise ValueError("内置 skill 不能卸载，只能停用。")
    try:
        row = await skill_store.get_installed(ctx.db, name)
    except skill_store.SkillNotFound:
        raise ValueError(f"skill 不存在：{name}") from None
    if row.source_type == "plugin_bundled":
        raise ValueError(f"「{name}」是插件内置 skill，不能卸载，只能停用。")
    return {
        "summary": f"卸载 skill「{name}」",
        "skill": {"name": row.name, "source_type": row.source_type, "source_url": row.source_url},
        "warning": "会删除 skill 文件，并从所有插件配置的 skill 选择中移除；不可恢复，需要时可重新安装。",
    }


async def uninstall_execute(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await skill_store.uninstall(
            ctx.db, _name(args), user_id=ctx.web_user_id, actor=f"system_agent:{ctx.channel}"
        )
    except (skill_store.SkillNotFound, skill_store.SkillConflict) as exc:
        raise ValueError(error_payload(exc)["message"]) from None
    return {**result, "business_changed": True}


_NAME_SCHEMA = {"type": "string", "description": "skill 名称（小写字母、数字、连字符）"}


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="skills.load",
            description=(
                "读取一个已安装 skill 的 SKILL.md 正文与文件清单。仅当用户请求与系统提示「已安装 Skills」"
                "中某个 skill 的用途相关时调用。"
            ),
            input_schema={
                "type": "object",
                "properties": {"name": _NAME_SCHEMA},
                "required": ["name"],
                "additionalProperties": False,
            },
            read_only=True,
            min_role="viewer",
            read_handler=load_skill,
        )
    )
    registry.register(
        ToolSpec(
            name="skills.read_file",
            description="读取已安装 skill 目录中的一个附属文本文件（SKILL.md 中引用的参考资料等）。",
            input_schema={
                "type": "object",
                "properties": {
                    "name": _NAME_SCHEMA,
                    "path": {"type": "string", "description": "skill 内相对路径，如 reference/forms.md"},
                },
                "required": ["name", "path"],
                "additionalProperties": False,
            },
            read_only=True,
            min_role="viewer",
            read_handler=read_skill_file,
        )
    )
    registry.register(
        ToolSpec(
            name="agent_skills.list",
            description="列出已安装的 Agent Skills 与内置技能：来源、版本/提交、对系统 Agent 与插件的启用范围。",
            input_schema={
                "type": "object",
                "properties": {"include_builtin": {"type": "boolean"}},
                "additionalProperties": False,
            },
            read_only=True,
            min_role="viewer",
            read_handler=list_skills,
        )
    )
    registry.register(
        ToolSpec(
            name="agent_skills.install",
            description=(
                "安装 Agent Skill（Anthropic SKILL.md 格式）：source=github（仓库或 /tree/<分支>/<子目录> 链接）、"
                "zip_url（公网 https zip）或 paste（粘贴 SKILL.md 全文）。预览会先拉取并校验。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": list(_AGENT_SOURCES)},
                    "url": {"type": "string"},
                    "ref": {"type": "string", "description": "可选分支/标签/提交"},
                    "path": {"type": "string", "description": "可选：仓库或 zip 内 skill 子目录"},
                    "skill_md": {"type": "string"},
                    "enabled_agent": {"type": "boolean"},
                    "enabled_plugins": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            read_only=False,
            min_role="admin",
            risk="dangerous",
            runtime_retryable=False,
            preview_handler=install_preview,
            execute_handler=install_execute,
        )
    )
    registry.register(
        ToolSpec(
            name="agent_skills.update",
            description="更新已安装 skill：GitHub / zip 链接来源按原地址重新拉取；粘贴来源需提供新的 skill_md。",
            input_schema={
                "type": "object",
                "properties": {"name": _NAME_SCHEMA, "skill_md": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            read_only=False,
            min_role="admin",
            risk="dangerous",
            runtime_retryable=False,
            preview_handler=update_preview,
            execute_handler=update_execute,
        )
    )
    registry.register(
        ToolSpec(
            name="agent_skills.set_enabled",
            description="分别启用/停用 skill 对系统 Agent（enabled_agent）与插件（enabled_plugins）的使用；内置技能只能切换系统 Agent。",
            input_schema={
                "type": "object",
                "properties": {
                    "name": _NAME_SCHEMA,
                    "enabled_agent": {"type": "boolean"},
                    "enabled_plugins": {"type": "boolean"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            read_only=False,
            min_role="admin",
            risk="normal",
            preview_handler=set_enabled_preview,
            execute_handler=set_enabled_execute,
        )
    )
    registry.register(
        ToolSpec(
            name="agent_skills.uninstall",
            description="卸载已安装 skill，并从所有插件配置中解绑；内置技能不可卸载。",
            input_schema={
                "type": "object",
                "properties": {"name": _NAME_SCHEMA},
                "required": ["name"],
                "additionalProperties": False,
            },
            read_only=False,
            min_role="admin",
            risk="dangerous",
            runtime_retryable=False,
            preview_handler=uninstall_preview,
            execute_handler=uninstall_execute,
        )
    )
