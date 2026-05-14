"""TG 内命令派发。

用户在 TG 中**自己给自己发**（任何对话，含收藏夹）以前缀（默认 ``,``）开头的消息时，
worker 拦截命令并**编辑原消息**为执行结果。

内置命令：``,help`` ``,status`` ``,ping`` ``,pause`` ``,resume`` ``,restart``（账号级）``,id``。
插件可以通过 ``register_plugin_command`` 追加额外命令（不会覆盖内置）。

Sprint2 #2 起新增 4 类"模板命令"：reply_text / forward_to / run_plugin / ai。
模板命令由主进程 DB 维护，worker 启动 / IPC reload 时拉取并合并到派发链路。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from telethon import TelegramClient, events

from ..db.base import AsyncSessionLocal
from ..services import audit as audit_svc
from ..redis_client import get_redis
from ..settings import settings
from ..util.sudo_permissions import (
    normalize_sudo_chat_ids,
    normalize_sudo_commands,
    sudo_scope_all,
)
from .ipc import CMD_PAUSE, CMD_RESUME, cmd_channel, make_cmd
from . import ai_runtime
from .commands.sudo_guard import (
    check_sudo_permission as _check_sudo_permission_impl,
    has_dispatch_target as _has_dispatch_target_impl,
    is_self_chat as _is_self_chat_impl,
    looks_like_command_name as _looks_like_command_name_impl,
    should_report_incoming_sudo_denial as _should_report_incoming_sudo_denial_impl,
)
from .commands.formatting import (
    _LONG_MESSAGE_SAFE_THRESHOLD,
    _LONG_MESSAGE_THRESHOLD,
    _dto_to_fake_row,
    _ensure_html_safe,
    _humanize_llm_error,
    _replied_media_placeholder,
    _safe_exception_text,
    _safe_log_text,
    _send_long_message,
    _split_long_message,
)

log = logging.getLogger(__name__)

BuiltinHandler = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class BuiltinCmd:
    handler: BuiltinHandler
    aliases: tuple[str, ...] = ()
    doc: str = ""


@dataclass(frozen=True)
class PluginCmd:
    """插件命令记录（用于追踪和注销）。"""

    handler: BuiltinHandler
    owner_plugin_key: str
    generation: int


_BUILTIN: dict[str, BuiltinCmd] = {}
_BUILTIN_ALIAS_TO_PRIMARY: dict[str, str] = {}
_PLUGIN_COMMANDS: dict[str, PluginCmd] = {}


@dataclass
class CommandContext:
    """worker-local 命令派发上下文。"""

    account_id: int
    templates: dict[str, dict[str, Any]]
    providers: dict[int, dict[str, Any]]
    command_prefix: str = ","
    aliases: dict[str, str] = None  # type: ignore[assignment]
    sudo_users: dict[int, dict[str, Any]] = None  # type: ignore[assignment]
    sudo_prefix: str = "."
    sudo_enabled: bool = False
    self_tg_user_id: int | None = None

    def __post_init__(self) -> None:
        if self.aliases is None:
            self.aliases = {}
        if self.sudo_users is None:
            self.sudo_users = {}


_ctx: CommandContext | None = None


def set_command_context(ctx: CommandContext) -> None:
    global _ctx
    _ctx = ctx


def get_command_context() -> CommandContext | None:
    return _ctx


def _format_sudo_chat_scope(values: Any) -> str:
    if sudo_scope_all(values):
        return "全部（显式）"
    chat_ids = normalize_sudo_chat_ids(values)
    return ",".join(str(chat_id) for chat_id in chat_ids) or "未授权"


def _format_sudo_command_scope(values: Any) -> str:
    if sudo_scope_all(values):
        return "全部（显式）"
    commands = normalize_sudo_commands(values)
    return ",".join(commands) or "未授权"


def builtin(name: str, *, aliases: tuple[str, ...] = (), doc: str = ""):
    """装饰器：把命令注册到 ``_BUILTIN``。"""

    def deco(fn):
        _BUILTIN[name] = BuiltinCmd(handler=fn, aliases=aliases, doc=doc)
        return fn

    return deco


def _register_builtin_aliases() -> None:
    _BUILTIN_ALIAS_TO_PRIMARY.clear()
    for name, item in _BUILTIN.items():
        _BUILTIN_ALIAS_TO_PRIMARY[name] = name
        for alias in item.aliases:
            _BUILTIN_ALIAS_TO_PRIMARY[alias] = name

async def _check_sudo_permission(event, cmd: str, account_id: int) -> tuple[bool, str]:
    return await _check_sudo_permission_impl(_ctx, event, cmd)


async def _write_sudo_audit_log(
    account_id: int,
    status: str,
    *,
    subcommand: str | None = None,
    row_count: int | None = None,
) -> None:
    """写入 sudo 查询审计日志；失败不影响命令语义。"""
    detail: dict[str, Any] = {"status": status}
    if subcommand is not None:
        detail["subcommand"] = subcommand
    if row_count is not None:
        detail["row_count"] = row_count
    try:
        async with AsyncSessionLocal() as db:
            await audit_svc.write(
                db,
                None,
                "worker.sudo",
                target=f"account:{account_id}",
                detail=detail,
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        log.warning(
            "写 sudo audit_log 失败 account_id=%s status=%s",
            account_id,
            status,
            exc_info=True,
        )


def _should_report_incoming_sudo_denial(error_msg: str) -> bool:
    return _should_report_incoming_sudo_denial_impl(error_msg)


def _looks_like_command_name(cmd: str, *, prefix: str = ".") -> bool:
    return _looks_like_command_name_impl(cmd, prefix=prefix)


def _has_dispatch_target(cmd: str, args_raw: str = "") -> bool:
    return _has_dispatch_target_impl(
        cmd,
        args_raw=args_raw,
        builtin_alias_to_primary=_BUILTIN_ALIAS_TO_PRIMARY,
        ctx=_ctx,
    )


def _is_self_chat(event) -> bool:
    return _is_self_chat_impl(event, ctx=_ctx)


@builtin("help", aliases=("h",), doc="显示可用命令列表")
async def _cmd_help(client, event, args, account_id):
    """列出所有可用命令及简短说明。

    每个 builtin 取其 docstring 第一行作为说明；插件注册的命令同样支持。
    模板命令也合并展示，标记 [模板]。
    """
    p = (_ctx.command_prefix if _ctx else "") or settings.command_prefix or ","
    try:
        raw_text = getattr(event, "raw_text", "")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        for probe in ("help", "h"):
            suffix = f"{probe}"
            if text.startswith(p) and text[len(p):].startswith(suffix):
                break
            idx = text.find(suffix)
            if idx > 0:
                p = text[:idx]
                break
    except Exception:  # noqa: BLE001
        pass
    lines = [f"📋 可用命令（前缀 `{p}`）：", "", "**内置：**"]
    for name in sorted(_BUILTIN.keys()):
        item = _BUILTIN[name]
        alias_text = f" ({', '.join(item.aliases)})" if item.aliases else ""
        desc = item.doc or "（无说明）"
        if name == "del":
            lines.append(f"• `{p}del N`{alias_text} — {desc}")
        else:
            lines.append(f"• `{p}{name}`{alias_text} — {desc}")
    # 模板命令（如有启用）
    if _ctx and _ctx.templates:
        lines.append("")
        lines.append("**自定义模板：**")
        shown: set[str] = set()
        for name in sorted(_ctx.templates.keys()):
            tpl = _ctx.templates[name]
            tid = int(tpl.get("id") or 0)
            if tid in shown:
                continue
            shown.add(tid)
            t = tpl.get("type", "?")
            desc = tpl.get("description") or f"模板：{t}"
            aliases = [a for a in (tpl.get("aliases") or []) if a != name]
            alias_text = f" / {p}" + f" / {p}".join(aliases) if aliases else ""
            lines.append(f"• `{p}{name}{alias_text}` — {desc}（[{t}]）")
    await event.edit("\n".join(lines))


@builtin("status", aliases=("s", "st"), doc="查看账号运行状态")
async def _cmd_status(client, event, args, account_id):
    """显示当前账号信息。"""
    import platform
    import sys

    import telethon

    from ..db.base import AsyncSessionLocal
    from ..db.models.account import Account, DeviceProfile, Proxy
    from ..db.models.system import SystemSetting

    me = await client.get_me()
    name = me.first_name or me.username or "<unnamed>"
    uname = f"@{me.username}" if getattr(me, "username", None) else "-"

    db_status = "-"
    proxy_text = "DIRECT"
    profile_text = "默认"
    prefix = (_ctx.command_prefix if _ctx else "") or settings.command_prefix or ","

    try:
        async with AsyncSessionLocal() as db:
            acc = await db.get(Account, account_id)
            if acc is not None:
                db_status = acc.status
                if acc.proxy_id:
                    pr = await db.get(Proxy, acc.proxy_id)
                    if pr is not None:
                        proxy_text = f"{pr.type}://{pr.host}:{pr.port}"
                if acc.device_profile_id:
                    dp = await db.get(DeviceProfile, acc.device_profile_id)
                    if dp is not None:
                        profile_text = f"{dp.name} · {dp.device_model} / {dp.system_version}"
            row = await db.get(SystemSetting, "command_prefix")
            if row is not None:
                raw = row.value
                if isinstance(raw, dict):
                    v = str(raw.get("value", "") or "").strip()
                    if v:
                        prefix = v
                elif isinstance(raw, str):
                    v = raw.strip()
                    if v:
                        prefix = v
    except Exception:  # noqa: BLE001
        pass

    tlv = getattr(telethon, "__version__", "?")
    text = (
        f"账号 #{account_id} · {name} ({uname})\n"
        f"在线状态：在线 ✓\n"
        f"DB 状态：{db_status}\n"
        f"命令前缀：`{prefix}`\n"
        f"代理：{proxy_text}\n"
        f"设备档案：{profile_text}\n"
        f"系统：{platform.system()} {platform.release()}\n"
        f"运行时：Python {sys.version.split()[0]} · Telethon {tlv}"
    )
    await event.edit(text)


@builtin("ping", doc="测试 worker 是否在线")
async def _cmd_ping(client, event, args, account_id):
    """连通性自检。"""
    await event.edit("pong")


@builtin("id", aliases=("i",), doc="返回当前会话 chat_id")
async def _cmd_id(client, event, args, account_id):
    """显示当前会话 chat_id（用于配置 auto_reply 的指定群）。"""
    chat_id = event.chat_id
    peer_kind = (
        "私聊" if event.is_private
        else "频道" if event.is_channel
        else "群" if event.is_group
        else "?"
    )
    # supergroup / channel：去掉 -100 前缀给一个"裸 id"，方便用户对照 t.me/c/<id> URL
    bare = ""
    a = abs(int(chat_id)) if chat_id is not None else 0
    if a > 1_000_000_000_000:
        bare = f"\n裸 id（去掉 -100 前缀）：{a - 1_000_000_000_000}"
    text = (
        f"类型：{peer_kind}\n"
        f"chat_id：{chat_id}{bare}\n\n"
        "把上面任一格式填到 auto_reply 规则的「指定群 ID」即可。"
    )
    await event.edit(text)


@builtin("pause", doc="暂停本账号")
async def _cmd_pause(client, event, args, account_id):
    """通过 IPC 通知本 worker 暂停主动动作。"""
    redis = get_redis()
    await redis.publish(cmd_channel(account_id), make_cmd(CMD_PAUSE))
    await event.edit("已暂停（仅暂停主动动作；被动接收照常）")


@builtin("resume", doc="恢复本账号")
async def _cmd_resume(client, event, args, account_id):
    """通过 IPC 通知本 worker 恢复主动动作。"""
    redis = get_redis()
    await redis.publish(cmd_channel(account_id), make_cmd(CMD_RESUME))
    await event.edit("已恢复")


@builtin("restart", aliases=("rs",), doc="重启本账号 worker")
async def _cmd_restart_account(client, event, args, account_id):
    """重启当前账号 worker（账号级，不影响其它账号与前后端）。"""
    await event.edit("正在重启本账号 worker...")
    await client.disconnect()


@builtin("version", aliases=("v",), doc="显示版本号")
async def _cmd_version(client, event, args, account_id):
    """显示当前 telebot 版本与运行环境。"""
    import platform
    import sys

    import telethon

    from .. import __version__

    tlv = getattr(telethon, "__version__", "?")
    text = (
        f"📦 telebot v{__version__}\n"
        f"Python {sys.version.split()[0]} · Telethon {tlv}\n"
        f"Platform {platform.system()} {platform.release()}"
    )
    await event.edit(text)


@builtin("del", doc="撤回自己最近 N 条消息（见 ,del N）")
async def _cmd_del(client, event, args, account_id):
    """撤回自己在当前会话最近发出的 N 条消息。"""
    if not args or not args[0].isdigit():
        await event.edit("用法：,del <数字>，例如 ,del 5")
        return
    n = int(args[0])
    if n <= 0 or n > 100:
        await event.edit("N 必须在 1-100 之间")
        return
    me = await client.get_me()
    chat = await event.get_chat()
    to_delete: list[int] = []
    async for msg in client.iter_messages(chat, limit=200, from_user=me.id):
        to_delete.append(msg.id)
        if len(to_delete) >= n + 1:
            break
    if not to_delete:
        await event.edit("没找到可撤回的消息")
        return
    await client.delete_messages(chat, to_delete[: n + 1])


@builtin("alias", doc="管理命令别名（set/del/ls）")
async def _cmd_alias(client, event, args, account_id):
    """命令别名管理。"""
    from sqlalchemy import delete, select

    from ..db.base import AsyncSessionLocal
    from ..db.models.command import CommandAlias

    if not args:
        await event.edit("用法：,alias set <别名> <目标> / ,alias del <别名> / ,alias ls")
        return

    sub = args[0]

    if sub in ("ls", "list"):
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(CommandAlias).where(
                        (CommandAlias.account_id == account_id)
                        | (CommandAlias.account_id.is_(None))
                    )
                )
            ).scalars().all()
        if not rows:
            await event.edit("当前没有任何别名")
            return
        lines = [f"• {r.alias} → {r.target}" for r in rows]
        await event.edit("命令别名列表：\n" + "\n".join(lines))
        return

    if sub == "del":
        alias_name = " ".join(args[1:]).strip()
        if not alias_name:
            await event.edit("用法：,alias del <别名>")
            return
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(CommandAlias).where(CommandAlias.alias == alias_name)
            )
            await db.commit()
        if result.rowcount:
            if _ctx is not None:
                _ctx.aliases.pop(alias_name, None)
            await event.edit(f"已删除别名：{alias_name}")
        else:
            await event.edit(f"别名 {alias_name!r} 不存在")
        return

    if sub == "set":
        rest = " ".join(args[1:]).strip()
        for sep in (" -> ", " → "):
            if sep in rest:
                parts = rest.split(sep, 1)
                alias_name = parts[0].strip()
                target_name = parts[1].strip()
                break
        else:
            tokens = rest.split()
            if len(tokens) < 2:
                await event.edit("用法：,alias set <别名> -> <目标命令>")
                return
            alias_name = tokens[0]
            target_name = " ".join(tokens[1:])

        if not alias_name or not target_name:
            await event.edit("别名和目标不能为空")
            return

        async with AsyncSessionLocal() as db:
            existing = (
                await db.execute(
                    select(CommandAlias).where(CommandAlias.alias == alias_name)
                )
            ).scalar_one_or_none()
            if existing:
                existing.target = target_name
            else:
                db.add(CommandAlias(alias=alias_name, target=target_name, account_id=account_id))
            await db.commit()
        if _ctx is not None:
            _ctx.aliases[alias_name] = target_name
        await event.edit(f"别名已设置：{alias_name} → {target_name}")
        return

    await event.edit(f"未知子命令：{sub}（支持 set/del/ls）")


@builtin("sudo", doc="查看 sudo 用户列表（ls）")
async def _cmd_sudo(client, event, args, account_id):
    """查看 sudo 用户（超级用户，可代表账号执行命令）授权摘要。"""
    from sqlalchemy import select

    from ..db.models.account import SudoUser

    if not args:
        await _write_sudo_audit_log(account_id, "usage")
        await event.edit("用法：,sudo ls（仅只读查询）")
        return

    sub = args[0].lower()

    if sub in ("ls", "list"):
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(SudoUser).where(SudoUser.account_id == account_id)
                )
            ).scalars().all()
        if not rows:
            await _write_sudo_audit_log(account_id, "empty", subcommand=sub, row_count=0)
            await event.edit("当前没有任何 sudo 用户")
            return
        lines = []
        for r in rows:
            chat_str = _format_sudo_chat_scope(r.allowed_chat_ids)
            cmd_str = _format_sudo_command_scope(r.allowed_commands)
            lines.append(f"• TG用户 {r.tg_user_id}（{r.display_name or '无'}）\n"
                         f"  允许对话：{chat_str}\n"
                         f"  允许命令：{cmd_str}")
        await _write_sudo_audit_log(
            account_id,
            "ok",
            subcommand=sub,
            row_count=len(rows),
        )
        await event.edit("Sudo 用户列表：\n" + "\n".join(lines))
        return

    await _write_sudo_audit_log(account_id, "invalid_subcommand", subcommand=sub)
    await event.edit("仅支持只读查询：,sudo ls")


_register_builtin_aliases()


def register_plugin_command(name: str, fn: Callable, *, owner_plugin_key: str = "", generation: int = 0):
    """允许其他模块（主要是 D Agent 插件）注册命令；不会覆盖内置。

    **安全：追踪 owner_plugin_key 和 generation，用于插件 reload/disable 时注销旧命令。**

    Args:
        name: 命令名（不含前缀）
        fn: 命令处理函数
        owner_plugin_key: 所属插件的 key
        generation: 插件实例的 generation
    """
    if name in _BUILTIN_ALIAS_TO_PRIMARY:
        return  # 不覆盖内置命令
    _BUILTIN[name] = BuiltinCmd(handler=fn)
    _PLUGIN_COMMANDS[name] = PluginCmd(
        handler=fn,
        owner_plugin_key=owner_plugin_key,
        generation=generation,
    )
    _register_builtin_aliases()


def unregister_plugin_command(name: str, *, owner_plugin_key: str | None = None):
    """注销插件命令。

    **安全设计**：
    - 如果指定了 owner_plugin_key，只注销该插件注册的命令
    - 如果未指定 owner_plugin_key，注销所有同名命令
    - 命令注销后，旧 handler 不会再被触发

    Args:
        name: 命令名
        owner_plugin_key: 如果指定，只注销该插件的命令
    """
    if name not in _PLUGIN_COMMANDS:
        # 内置命令不在插件命令表中，不能被注销。
        return

    # 从插件命令表中注销
    pcmd = _PLUGIN_COMMANDS[name]
    if owner_plugin_key is None or pcmd.owner_plugin_key == owner_plugin_key:
        _PLUGIN_COMMANDS.pop(name, None)
        # 插件命令复用 _BUILTIN 分发表；注销时必须一起移除。
        _BUILTIN.pop(name, None)
        _register_builtin_aliases()


def unregister_all_plugin_commands(*, owner_plugin_key: str):
    """注销指定插件注册的所有命令。

    Args:
        owner_plugin_key: 插件的 key
    """
    to_remove = [
        name for name, pcmd in _PLUGIN_COMMANDS.items()
        if pcmd.owner_plugin_key == owner_plugin_key
    ]
    for name in to_remove:
        unregister_plugin_command(name, owner_plugin_key=owner_plugin_key)


# ════════════════════════════════════════════════════════════
# 模板命令执行（Sprint2 #2）
# ════════════════════════════════════════════════════════════


async def _run_template(client, event, args, tpl: dict[str, Any], account_id: int) -> None:
    """根据 ``tpl["type"]`` 分支执行模板命令。

    模板 dict 的字段（与 ``CommandTemplate`` 模型对应）：
    - ``id``        模板 id
    - ``name``      命令名（不含前缀）
    - ``type``      reply_text / forward_to / run_plugin / ai
    - ``config``    按 type 不同结构
    - ``description``  可选
    """
    t = tpl.get("type")
    cfg: dict[str, Any] = tpl.get("config") or {}

    if t == "reply_text":
        # 简单变量替换：{args} → 用户拼接的剩余参数
        text = str(cfg.get("text", "")).replace("{args}", " ".join(args))
        await event.edit(text or "(空文本)")
        return

    if t == "forward_to":
        replied = await event.get_reply_message()
        if not replied:
            await event.edit("✗ 请回复要转发的消息再用此命令")
            return
        # target_chat_id 留空 / 缺省 → 转发到触发命令所在的 chat
        raw_target = cfg.get("target_chat_id")
        if raw_target is None or raw_target == "":
            target = event.chat_id
        else:
            try:
                target = int(raw_target)
            except (ValueError, TypeError):
                await event.edit("✗ 模板配置错误：target_chat_id 不是合法的整数")
                return
        # 按 mode 分支处理
        mode = cfg.get("mode", "forward_native")
        try:
            if mode == "forward_native":
                await replied.forward_to(target)
            elif mode == "copy_text":
                text = replied.text or "(empty)"
                await event.client.send_message(target, text)
            elif mode == "quote":
                try:
                    src = await replied.get_chat()
                except Exception:  # noqa: BLE001
                    src = None
                chat_label = (
                    getattr(src, "title", None)
                    or getattr(src, "username", None)
                    or getattr(src, "first_name", None)
                    or str(replied.chat_id if hasattr(replied, "chat_id") else "?")
                )
                body = f"📨 来自 {chat_label}\n\n{replied.text or '(no text)'}"
                await event.client.send_message(target, body)
            elif mode == "link_only":
                # 为 replied 构造 link：取 replied 的 chat_id + message.id
                cid = getattr(replied, "chat_id", None)
                mid = getattr(replied, "id", None)
                if cid and mid:
                    sid = str(cid)
                    if sid.startswith("-100"):
                        link = f"https://t.me/c/{sid[4:]}/{mid}"
                    else:
                        link = f"消息引用：chat={cid}, id={mid}"
                else:
                    link = "消息引用：无法生成链接"
                await event.client.send_message(target, link)
            else:
                await event.edit(f"✗ 未知转发方式：{mode}")
                return
        except Exception as e:  # noqa: BLE001
            await event.edit(f"✗ 转发失败：{type(e).__name__}: {str(e)[:80]}")
            return
        mode_label = {"forward_native": "转发", "copy_text": "复制文本", "quote": "引用转发", "link_only": "链接"}.get(mode, mode)
        await event.edit(f"✓ 已{mode_label}到 {target}")
        # 自动删除命令消息
        delete_immediately = cfg.get("delete_immediately")
        if delete_immediately:
            import asyncio as _aio

            async def _delete_now() -> None:
                try:
                    await event.delete()
                except Exception:  # noqa: BLE001
                    pass

            _aio.create_task(_delete_now())
        else:
            delete_after_raw = cfg.get("delete_after")
            if delete_after_raw:
                try:
                    seconds = int(delete_after_raw)
                except (ValueError, TypeError):
                    seconds = 0
                if seconds > 0:
                    import asyncio as _aio

                    async def _delete_later() -> None:
                        try:
                            await _aio.sleep(seconds)
                            await event.delete()
                        except Exception:  # noqa: BLE001
                            # TG 端权限/网络异常都不影响主流程
                            pass

                    _aio.create_task(_delete_later())
        return

    if t == "ai":
        await _run_ai(client, event, args, tpl, account_id)
        return

    if t == "run_plugin":
        plugin_key = str(cfg.get("plugin_key") or "").strip()
        method = str(cfg.get("method") or cfg.get("command") or plugin_key).strip()
        if not plugin_key or not method:
            await event.edit("✗ run_plugin 需要配置 plugin_key 和 method/command")
            return
        pcmd = _PLUGIN_COMMANDS.get(method)
        if pcmd is None or pcmd.owner_plugin_key != plugin_key:
            await event.edit(f"✗ 插件命令不可用：{plugin_key}.{method}")
            return
        await pcmd.handler(client, event, args, account_id)
        return

    await event.edit(f"✗ 未知模板类型：{t}")


async def _run_ai(client, event, args, tpl: dict[str, Any], account_id: int) -> None:
    await ai_runtime.invoke(client, event, args, tpl, account_id)


def make_command_handler(client: TelegramClient, account_id: int, prefix: str | None = None):
    """创建并注册 TG 命令派发 handler。

    普通命令监听 ``outgoing=True``；sudo 命令额外监听 ``incoming=True``，
    允许白名单用户用 sudo_prefix 触发命令。

    前缀热加载：handler 每次拦截消息时**从 ctx 读 prefix**，不再用闭包里固定 pattern。
    系统设置改前缀 → 主进程广播 IPC ``reload_global`` → runtime 重拉 ctx → 下一条消息立刻按
    新前缀匹配。``prefix`` 参数仅作"启动期默认"，正常运行靠 ctx 动态。
    """
    fallback_prefix = prefix or settings.command_prefix or ","

    class _IncomingSudoEvent:
        """把 incoming sudo 的 edit() 转成 respond()，避免尝试编辑他人消息。"""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def edit(self, *args, **kwargs):
            responder = getattr(self._inner, "respond", None) or getattr(self._inner, "reply", None)
            if responder is None:
                return None
            return await responder(*args, **kwargs)

    async def _dispatch(event, cmd: str, args_raw: str, *, help_prefix: str) -> None:
        args = args_raw.split() if args_raw else []
        # 1. 内置命令优先
        primary = _BUILTIN_ALIAS_TO_PRIMARY.get(cmd)
        item = _BUILTIN.get(primary) if primary else None
        if item is not None:
            try:
                await item.handler(client, event, args, account_id)
            except Exception as e:  # noqa: BLE001
                # 命令执行异常时，把错误原地写回消息，方便排查（消息已脱敏：去路径/token）
                try:
                    await event.edit(f"✗ 执行失败：{_safe_exception_text(e)}")
                except Exception:
                    pass
            return

        # 2. 别名解析（贪心最长匹配）
        if _ctx is not None and _ctx.aliases:
            # 尝试从 "cmd arg1 arg2..." 中匹配最长的别名
            full_rest = f"{cmd} {args_raw}".strip() if args_raw else cmd
            matched_alias: str | None = None
            for alias in sorted(_ctx.aliases.keys(), key=len, reverse=True):
                if full_rest == alias or full_rest.startswith(alias + " "):
                    matched_alias = alias
                    break
            if matched_alias is not None:
                target = _ctx.aliases[matched_alias]
                remaining = full_rest[len(matched_alias):].strip()
                # 重新拼接：target + remaining args
                new_text = f"{target} {remaining}".strip() if remaining else target
                new_parts = new_text.split(None, 1)
                new_cmd = new_parts[0] if new_parts else ""
                new_args_raw = new_parts[1] if len(new_parts) > 1 else ""
                new_args = new_args_raw.split() if new_args_raw else []
                # 重新派发到 builtin
                primary2 = _BUILTIN_ALIAS_TO_PRIMARY.get(new_cmd)
                item2 = _BUILTIN.get(primary2) if primary2 else None
                if item2 is not None:
                    try:
                        await item2.handler(client, event, new_args, account_id)
                    except Exception as e:  # noqa: BLE001
                        try:
                            await event.edit(f"✗ 执行失败：{_safe_exception_text(e)}")
                        except Exception:
                            pass
                    return
                # 重新派发到模板
                tpl2 = _ctx.templates.get(new_cmd)
                if tpl2 is not None:
                    try:
                        await _run_template(client, event, new_args, tpl2, account_id)
                    except Exception as e:  # noqa: BLE001
                        try:
                            await event.edit(f"✗ 执行失败：{_safe_exception_text(e)}")
                        except Exception:
                            pass
                    return

        # 3. 模板命令（按 name 查 worker-local ctx）
        if _ctx is not None:
            tpl = _ctx.templates.get(cmd)
            if tpl is not None:
                try:
                    await _run_template(client, event, args, tpl, account_id)
                except Exception as e:  # noqa: BLE001
                    try:
                        await event.edit(f"✗ 执行失败：{_safe_exception_text(e)}")
                    except Exception:
                        pass
                return

        # 4. 未知命令
        try:
            await event.edit(f"未知命令：{cmd}（{help_prefix}help 查看可用列表）")
        except Exception:
            pass

    async def _handle(event, *, allow_normal: bool, incoming_sudo: bool = False):
        text = event.raw_text or ""
        sudo_p = (_ctx.sudo_prefix if _ctx else "") or "."
        pattern_sudo = re.compile(rf"^{re.escape(sudo_p)}(\S+)(?:\s+(.*))?$", re.S)
        m = pattern_sudo.match(text)
        if m and incoming_sudo:
            cmd = m.group(1)
            args_raw = (m.group(2) or "").strip()
            if not _looks_like_command_name(cmd, prefix=sudo_p):
                return
            if incoming_sudo and not _has_dispatch_target(cmd, args_raw):
                return
            if incoming_sudo and not _is_self_chat(event):
                return
            allowed, error_msg = await _check_sudo_permission(event, cmd, account_id)
            if not allowed:
                if incoming_sudo:
                    if not _should_report_incoming_sudo_denial(error_msg):
                        return
                    try:
                        await event.respond(f"✗ Sudo 权限拒绝：{error_msg}")
                    except Exception:
                        pass
                else:
                    await event.edit(f"✗ Sudo 权限拒绝：{error_msg}")
                return
            dispatch_event = _IncomingSudoEvent(event) if incoming_sudo else event
            await _dispatch(dispatch_event, cmd, args_raw, help_prefix=sudo_p)
            return

        if not allow_normal:
            return

        p = (_ctx.command_prefix if _ctx else "") or fallback_prefix
        pattern = re.compile(rf"^{re.escape(p)}(\S+)(?:\s+(.*))?$", re.S)
        m = pattern.match(text)
        if not m:
            return
        cmd = m.group(1)
        if not _looks_like_command_name(cmd, prefix=p):
            return
        await _dispatch(event, cmd, (m.group(2) or "").strip(), help_prefix=p)

    @client.on(events.NewMessage(incoming=True))
    async def _sudo_incoming_h(event):
        await _handle(event, allow_normal=False, incoming_sudo=True)

    @client.on(events.NewMessage(outgoing=True))
    async def _h(event):
        await _handle(event, allow_normal=True, incoming_sudo=False)

    return _h
