"""TG 内命令派发。

用户在 TG 中**自己给自己发**（任何对话，含收藏夹）以前缀（默认 ``,``）开头的消息时，
worker 拦截命令并**编辑原消息**为执行结果。

内置命令：``,help`` ``,status`` ``,ping`` ``,pause`` ``,resume`` ``,reboot``（项目级）``,restart``（账号级）``,id``。
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

from ..redis_client import get_redis
from ..settings import settings
from .ipc import CMD_PAUSE, CMD_RESUME, cmd_channel, make_cmd

log = logging.getLogger(__name__)

BuiltinHandler = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class BuiltinCmd:
    handler: BuiltinHandler
    aliases: tuple[str, ...] = ()
    doc: str = ""


# key 是主命令名（不含前缀）
_BUILTIN: dict[str, BuiltinCmd] = {}
# key 是"主命令 + alias"全集，value 是主命令名
_BUILTIN_ALIAS_TO_PRIMARY: dict[str, str] = {}


# ── 模板命令派发上下文 ──────────────────────────────────────────
# 由 runtime.py 在 worker 启动 / IPC reload 时填充；handler 直接读
@dataclass
class CommandContext:
    """worker-local 命令派发上下文。

    - ``account_id``      当前 worker 服务的账号 id
    - ``templates``       {模板名: 模板 dict}；模板 dict 由 ``runtime.py`` 从 DB 拉出后投递
    - ``providers``       {provider_id: provider dict}；同样从 DB 拉，含 api_key 加密 token
    - ``command_prefix``  当前生效的命令前缀（``,`` / ``-`` / ``/`` 等）；
                          系统设置改了 → 主进程发 IPC 让 ``runtime`` 重拉，再写到这里
                          → handler 每次匹配时从 ctx 取，所以前缀热加载对已注册 handler 也生效
    """

    account_id: int
    templates: dict[str, dict[str, Any]]
    providers: dict[int, dict[str, Any]]
    command_prefix: str = ","
    aliases: dict[str, str] = None  # type: ignore[assignment]  # {alias: target}
    sudo_users: dict[int, dict[str, Any]] = None  # type: ignore[assignment]  # {tg_user_id: config}
    sudo_prefix: str = "."

    def __post_init__(self) -> None:
        if self.aliases is None:
            self.aliases = {}
        if self.sudo_users is None:
            self.sudo_users = {}


# 全局 ctx 由 runtime.py 在 worker 进程启动时初始化并通过闭包传给 handler；
# 同一进程只服务一个 account_id，所以可以直接用模块级单例
_ctx: CommandContext | None = None


def set_command_context(ctx: CommandContext) -> None:
    """runtime.py 启动 worker 后调用一次，IPC reload 时也调用更新内容。"""
    global _ctx
    _ctx = ctx


def get_command_context() -> CommandContext | None:
    """主要供测试 / 调试使用。"""
    return _ctx


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


def _safe_exception_text(e: BaseException, max_len: int = 200) -> str:
    """把异常信息净化成"安全可在 TG 里显示"的短字符串。

    具体做：
    - 去掉文件绝对路径（``/Users/.../foo.py`` / ``C:\\...\\foo.py``）—— 暴露目录结构是
      安全 & 隐私问题（用户截图里就泄漏过 ``/Users/anoyou/Desktop/telebot/...``）
    - 去掉 ``sk-`` / ``Bearer xxx`` 一类 token 字样
    - 截断到 ``max_len`` 字符
    """
    import re

    msg = f"{type(e).__name__}: {e}"
    # 去 unix 绝对路径 (含括号包裹的也匹配)
    msg = re.sub(r"\(?/[^()\s'\"]+\.py\)?", "<path>", msg)
    # 去 windows 绝对路径
    msg = re.sub(r"\(?[A-Za-z]:[\\/][^()\s'\"]+\.py\)?", "<path>", msg)
    # 去常见 token
    msg = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted>", msg)
    msg = re.sub(r"Bearer\s+[A-Za-z0-9_.\-]{8,}", "Bearer <redacted>", msg)
    if len(msg) > max_len:
        msg = msg[:max_len] + "…"
    return msg


async def _check_sudo_permission(event, cmd: str, account_id: int) -> tuple[bool, str]:
    """检查 sudo 权限。
    
    Returns:
        (allowed, error_message)
    """
    if _ctx is None or not _ctx.sudo_users:
        return False, "sudo 系统未配置"
    
    sender = await event.get_sender()
    tg_user_id = getattr(sender, "id", None)
    if tg_user_id is None:
        return False, "无法识别发送者"
    
    sudo_config = _ctx.sudo_users.get(tg_user_id)
    if sudo_config is None:
        return False, f"TG 用户 {tg_user_id} 不在 sudo 列表中"
    
    # 检查 chat_id 白名单
    allowed_chats = sudo_config.get("allowed_chat_ids", [])
    if allowed_chats:  # 空列表 = 所有对话
        chat_id = event.chat_id
        if chat_id not in allowed_chats:
            return False, f"此对话（chat_id={chat_id}）不在白名单中"
    
    # 检查命令白名单
    allowed_cmds = sudo_config.get("allowed_commands", [])
    if allowed_cmds:  # 空列表 = 所有命令
        if cmd not in allowed_cmds:
            return False, f"命令 `{cmd}` 不在白名单中"
    
    return True, ""


def _replied_media_placeholder(msg: Any) -> str:
    """被回复消息没正文（媒体类）时返回个 emoji+标签占位字符串。

    用途：
    - UI 上 ``{quoted}`` blockquote 不至于显示空白
    - LLM 收到 ``[原文]\\n📷 [图片]`` 时知道用户在问图，能体面地说"我看不到图片"

    支持的媒体类型与 telethon 1.36 ``Message`` 上对应字段同名（``photo`` / ``video`` /
    ``voice`` / ``sticker`` / ``audio`` / ``gif`` / ``document`` / ``geo`` / ``contact``
    / ``poll`` / ``video_note``）。匹配不到任何媒体返回空串。
    """
    if getattr(msg, "photo", None) is not None:
        return "📷 [图片]"
    if getattr(msg, "video_note", None) is not None:
        return "📹 [视频留言]"
    if getattr(msg, "video", None) is not None:
        return "🎬 [视频]"
    if getattr(msg, "voice", None) is not None:
        return "🎤 [语音]"
    if getattr(msg, "sticker", None) is not None:
        return "[贴纸]"
    if getattr(msg, "audio", None) is not None:
        return "🎵 [音频]"
    if getattr(msg, "gif", None) is not None:
        return "🖼️ [GIF]"
    if getattr(msg, "document", None) is not None:
        return "📎 [文件]"
    if getattr(msg, "geo", None) is not None:
        return "📍 [位置]"
    if getattr(msg, "contact", None) is not None:
        return "👤 [联系人]"
    if getattr(msg, "poll", None) is not None:
        return "📊 [投票]"
    return ""


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


@builtin("reboot", aliases=("rb",), doc="重启整个项目（等效 make restart）")
async def _cmd_restart_project(client, event, args, account_id):
    """触发项目级重启：后台异步执行 ``make restart``，当前会话会短暂中断。"""
    import asyncio
    from pathlib import Path

    # .../backend/app/worker/command.py -> 项目根目录
    root = Path(__file__).resolve().parents[3]
    cmd = f"cd {root} && nohup make restart >> logs/restart-trigger.log 2>&1 &"
    await event.edit("已触发项目重启（make restart），服务会短暂中断 10-30 秒。")
    await asyncio.create_subprocess_exec("/bin/zsh", "-lc", cmd, start_new_session=True)


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


@builtin("sudo", doc="管理 sudo 用户（add/del/ls）")
async def _cmd_sudo(client, event, args, account_id):
    """管理 sudo 用户（超级用户，可代表账号执行命令）。"""
    from sqlalchemy import delete, select

    from ..db.base import AsyncSessionLocal
    from ..db.models.account import SudoUser

    if not args:
        await event.edit("用法：,sudo add <tg_user_id> [--display-name <name>] [--chat-ids <id1,id2>] [--commands <cmd1,cmd2>]\n"
                       "     ,sudo del <tg_user_id>\n"
                       "     ,sudo ls")
        return

    sub = args[0]

    if sub in ("ls", "list"):
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(SudoUser).where(SudoUser.account_id == account_id)
                )
            ).scalars().all()
        if not rows:
            await event.edit("当前没有任何 sudo 用户")
            return
        lines = []
        for r in rows:
            chat_str = ",".join(str(c) for c in (r.allowed_chat_ids or [])) or "全部"
            cmd_str = ",".join(r.allowed_commands or []) or "全部"
            lines.append(f"• TG用户 {r.tg_user_id}（{r.display_name or '无'}）\n"
                         f"  允许对话：{chat_str}\n"
                         f"  允许命令：{cmd_str}")
        await event.edit("Sudo 用户列表：\n" + "\n".join(lines))
        return

    if sub == "del":
        if len(args) < 2:
            await event.edit("用法：,sudo del <tg_user_id>")
            return
        try:
            tg_user_id = int(args[1])
        except ValueError:
            await event.edit(f"无效的 tg_user_id：{args[1]}")
            return
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(SudoUser).where(
                    SudoUser.account_id == account_id,
                    SudoUser.tg_user_id == tg_user_id
                )
            )
            await db.commit()
        if result.rowcount:
            if _ctx is not None and _ctx.sudo_users:
                _ctx.sudo_users.pop(tg_user_id, None)
            await event.edit(f"已删除 sudo 用户：TG用户 {tg_user_id}")
        else:
            await event.edit(f"sudo 用户 {tg_user_id} 不存在")
        return

    if sub == "add":
        if len(args) < 2:
            await event.edit("用法：,sudo add <tg_user_id> [--display-name <name>] [--chat-ids <id1,id2>] [--commands <cmd1,cmd2>]")
            return
        try:
            tg_user_id = int(args[1])
        except ValueError:
            await event.edit(f"无效的 tg_user_id：{args[1]}")
            return
        
        # 解析可选参数
        display_name = None
        allowed_chat_ids: list[int] = []
        allowed_commands: list[str] = []
        
        rest_args = args[2:]
        i = 0
        while i < len(rest_args):
            if rest_args[i] == "--display-name" and i + 1 < len(rest_args):
                display_name = rest_args[i + 1]
                i += 2
            elif rest_args[i] == "--chat-ids" and i + 1 < len(rest_args):
                chat_str = rest_args[i + 1]
                try:
                    allowed_chat_ids = [int(x.strip()) for x in chat_str.split(",")]
                except ValueError:
                    await event.edit(f"无效的 chat_ids：{chat_str}")
                    return
                i += 2
            elif rest_args[i] == "--commands" and i + 1 < len(rest_args):
                cmd_str = rest_args[i + 1]
                allowed_commands = [x.strip() for x in cmd_str.split(",")]
                i += 2
            else:
                i += 1
        
        async with AsyncSessionLocal() as db:
            existing = (
                await db.execute(
                    select(SudoUser).where(
                        SudoUser.account_id == account_id,
                        SudoUser.tg_user_id == tg_user_id
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.display_name = display_name
                existing.allowed_chat_ids = allowed_chat_ids
                existing.allowed_commands = allowed_commands
            else:
                db.add(SudoUser(
                    account_id=account_id,
                    tg_user_id=tg_user_id,
                    display_name=display_name,
                    allowed_chat_ids=allowed_chat_ids,
                    allowed_commands=allowed_commands,
                ))
            await db.commit()
        
        await event.edit(f"已添加/更新 sudo 用户：TG用户 {tg_user_id}（{display_name or '无'}）")
        return

    await event.edit(f"未知子命令：{sub}（支持 add/del/ls）")


_register_builtin_aliases()


def register_plugin_command(name: str, fn: Callable):
    """允许其他模块（主要是 D Agent 插件）注册命令；不会覆盖内置。"""
    if name in _BUILTIN_ALIAS_TO_PRIMARY:
        return  # 不覆盖
    _BUILTIN[name] = BuiltinCmd(handler=fn)
    _register_builtin_aliases()


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
        # V1 占位：等 Sprint2 #4 插件模块化完成后再接
        await event.edit(
            f"⏳ run_plugin 占位：插件={cfg.get('plugin_key')!r}, 方法={cfg.get('method')!r}"
        )
        return

    await event.edit(f"✗ 未知模板类型：{t}")


async def _run_ai(client, event, args, tpl: dict[str, Any], account_id: int) -> None:
    """AI 类命令：调 LLM provider，把回答编辑回原消息。

    工作模式（cfg.routing_mode）：
    - ``fixed``（默认）— 用 cfg.provider_id 锁定的 provider
    - ``auto``        — 调 services.llm_router 按消息内容自动选 provider；
                       配置项：``routing_fallback_provider_id`` / ``classifier_provider_id``
                       自动路由失败兜底走 cfg.provider_id 自身，再不行才报错

    安全要求：
    - api_key 仅在 ``LLMClient.__init__`` 中持有；不打 log、不写 audit
    - 任何异常路径只透出 ``type(e).__name__`` 与裁剪后的 message
    """
    cfg: dict[str, Any] = tpl.get("config") or {}
    provider_id = cfg.get("provider_id")
    if provider_id is None:
        await event.edit("✗ AI 命令未配置 provider_id（系统设置 → LLM Provider 里建一个，填回此处）")
        return

    if _ctx is None:
        await event.edit("✗ worker 命令上下文尚未初始化")
        return

    # 每次 AI 调用前从 DB 刷新 provider 缓存，保证新建/修改/删除的 provider 立即可用。
    # Redis Pub/Sub 是 fire-and-forget，IPC 通知可能丢失导致 _ctx.providers 永远过期。
    # AI 命令本身要调 LLM（耗时 1–30s），额外一次轻量 DB SELECT（~1ms）开销可忽略。
    #
    # 0.5.1：刷新失败时**不再静默吞**——log.exception 让真实异常进 worker log。
    # 此前用户报"新增 provider 后 ,ai @list 看不到"，根因是 _refresh_command_context
    # 静默失败导致 _ctx 永远是老快照。改成显式 log 后用户在「日志中心 → 系统」就能看到。
    try:
        from .runtime import _refresh_command_context  # noqa: F811  # lazy import 避免循环依赖
        await _refresh_command_context(_ctx.account_id)
    except Exception:  # noqa: BLE001
        log.exception("[ai] 刷新 provider 缓存失败 account=%s", _ctx.account_id)
        # 刷新失败不阻塞命令执行，继续用内存缓存；下次 ,ai 再试

    # ── inline @override：本次调用临时覆盖 provider/model/路由模式 ─────
    # 解析 args[0]：@<name>[:<model>] / @auto / @list；非 @ 开头则不动。
    # 这一步必须放在"决策 provider_id"之前——它会把模板里配的 fixed
    # provider_id 替换成用户指定的，并可能改 routing_mode。
    from .inline_override import (
        InlineOverrideError,
        format_provider_list,
        parse_inline_override,
    )

    inline_provider_override: int | None = None
    inline_model_override: str | None = None
    inline_force_auto = False
    # 当前命令前缀 + 模板名——给"用法"提示用，避免硬编码 ",ai"。
    # 若 _ctx.command_prefix 没设置就退回默认 ","；template name 取触发模板名。
    _cmd_prefix = (_ctx.command_prefix if _ctx else None) or ","
    _tpl_name = str(tpl.get("name") or "ai")
    try:
        inline, args = parse_inline_override(
            args, _ctx.providers,
            cmd_prefix=_cmd_prefix, template_name=_tpl_name,
        )
    except InlineOverrideError as e:
        await event.edit(str(e))
        return
    if inline is not None:
        if inline.kind == "list":
            # 直接给可用列表——不调 LLM、不消费 tokens
            await event.edit(
                format_provider_list(
                    _ctx.providers,
                    cmd_prefix=_cmd_prefix, template_name=_tpl_name,
                )
            )
            return
        if inline.kind == "refresh":
            try:
                from .runtime import _refresh_command_context  # lazy import avoid cycle

                await _refresh_command_context(_ctx.account_id)
            except Exception as e:  # noqa: BLE001
                log.exception("[ai] 手动刷新 provider 缓存失败 account=%s", _ctx.account_id)
                await event.edit(f"✗ 刷新 provider 缓存失败：{type(e).__name__}: {e}")
                return
            await event.edit(
                "✓ provider 缓存已刷新\n\n"
                + format_provider_list(
                    _ctx.providers,
                    cmd_prefix=_cmd_prefix, template_name=_tpl_name,
                )
            )
            return
        if inline.kind == "auto":
            inline_force_auto = True
        else:  # provider
            inline_provider_override = inline.provider_id
            inline_model_override = inline.model

    # ── 拼 prompt 上下文（路由器与 LLM 都要看消息内容）─────────
    # 图片来源有 2×N 条：
    #   A) 被回复消息及其所在相册的全部图（,ai 的传统语义："回复某条→问那一条"）
    #   B) 命令消息自身及其所在相册的全部图（caption 触发模式：图 + ",ai 这是什么"）
    # 涵盖：photo / image-as-document（按文件发送的未压缩图）/ 静态贴纸（webp）。
    # 转发媒体的 file_reference 过期会自动重拉一次（见 media.download_image_bytes）。
    from .media import (
        collect_image_sources,
        download_audio_bytes,
        download_image_bytes,
        message_has_audio,
        message_has_image,
    )

    user_q = " ".join(args).strip()
    replied = await event.get_reply_message()
    quote = bool(cfg.get("quote_replied", True))
    replied_text: str | None = None
    if replied is not None:
        original = replied.text or replied.message or ""
        # 被回复消息没正文时（媒体类）给个 emoji+标签占位——同时也喂给 LLM，让它知道
        # 用户在问图/视频等不可读的内容，模型可以体面地说"我看不到这张图，你能描述吗"
        if not original:
            original = _replied_media_placeholder(replied)
        replied_text = original or None
    self_msg = getattr(event, "message", None)
    has_replied_image = message_has_image(replied)
    has_self_image = message_has_image(self_msg)
    has_any_image = has_replied_image or has_self_image
    has_replied_audio = message_has_audio(replied)
    has_self_audio = message_has_audio(self_msg)
    has_any_audio = has_replied_audio or has_self_audio
    log.warning(
        "[ai-debug] replied=%s text=%r q=%r img(replied=%s,self=%s) audio(replied=%s,self=%s)",
        replied is not None, replied_text, user_q,
        has_replied_image, has_self_image, has_replied_audio, has_self_audio,
    )

    # ── 决策 provider_id（fixed / auto）────────────────────────
    # inline @override 在前面已解析；优先级：
    #   inline @<provider> 覆盖 cfg.routing_mode → 强制 fixed 走该 provider
    #   inline @auto       覆盖 cfg.routing_mode → 强制 auto
    #   都没给                按 cfg.routing_mode
    if inline_force_auto:
        routing_mode = "auto"
    elif inline_provider_override is not None:
        routing_mode = "fixed"
    else:
        routing_mode = str(cfg.get("routing_mode") or "fixed").lower()
    routing_note: str | None = None  # 自动路由时附加在结尾的说明
    chosen_provider_id = (
        inline_provider_override
        if inline_provider_override is not None
        else int(provider_id)
    )

    if routing_mode == "auto":
        # 局部 import 避免 worker 启动时强依赖
        from ..services.llm_router import pick_provider

        cls_id = cfg.get("classifier_provider_id")
        # 没显式配兜底就用 fixed 那条；保证 auto 模式失败也有 last resort
        fb_id = cfg.get("routing_fallback_provider_id") or provider_id
        try:
            decision = await pick_provider(
                user_q,
                replied_text,
                has_any_image,  # 替代原先只看 replied 的标志，让 self/album/document 都能命中视觉路由
                _ctx.providers,
                classifier_provider_id=int(cls_id) if cls_id else None,
                fallback_provider_id=int(fb_id),
            )
        except ValueError as e:
            # 路由器找不到任何可用 provider
            await event.edit(f"✗ AI 路由失败：{e}")
            return
        except Exception as e:  # noqa: BLE001
            # 任何意外都不要让命令静默卡住
            await event.edit(f"✗ AI 路由异常：{type(e).__name__}: {str(e)[:120]}")
            return
        chosen_provider_id = decision.provider_id
        routing_note = f"auto · {decision.reason}"
    elif inline_provider_override is not None:
        # 给 footer 一个标记，让用户知道是 inline 覆盖来的（而不是模板默认）
        prov_name = _ctx.providers.get(chosen_provider_id, {}).get("name") or chosen_provider_id
        routing_note = f"inline → @{prov_name}"

    provider_dict = _ctx.providers.get(chosen_provider_id)
    if provider_dict is None:
        # 兜底自愈：上下文可能过期，现场强刷一次再查（避免"刚新增 provider 就说不存在"）
        try:
            from .runtime import _refresh_command_context  # lazy import avoid cycle

            await _refresh_command_context(_ctx.account_id)
            provider_dict = _ctx.providers.get(chosen_provider_id)
        except Exception as e:  # noqa: BLE001
            log.exception("[ai] provider miss 时刷新失败 account=%s pid=%s", _ctx.account_id, chosen_provider_id)
            await event.edit(f"✗ provider 刷新失败：{type(e).__name__}: {e}")
            return

    if provider_dict is None:
        await event.edit(
            f"✗ provider_id={chosen_provider_id} 不存在或未加载\n\n"
            + format_provider_list(_ctx.providers, cmd_prefix=_cmd_prefix, template_name=_tpl_name)
        )
        return

    # ── 视觉数据：聚合所有源（replied + self + album）→ 下载 → 喂给 vision ─
    # 反幻觉守卫：只有当 chosen provider 的 modality 在 {vision, multimodal} 才下载并发送图片；
    # 否则**显式拒答**，绝不让纯文本模型对着 "📷 [图片]" 占位符瞎编。
    chosen_modality = str(provider_dict.get("modality") or "text").lower()
    provider_supports_vision = chosen_modality in ("vision", "multimodal")
    provider_supports_audio = chosen_modality in ("audio", "multimodal")
    image_bytes_list: list[bytes] = []
    image_msgs: list[Any] = []
    if has_any_image:
        if not provider_supports_vision:
            # fixed 模式下用户绑了纯文本模型；auto 模式下规则也没把它路由到 vision provider
            # —— 不论哪种情况，让模型对着不存在的图片瞎答都是有害的，直接告诉用户
            tip = (
                f"✗ 消息含图，但当前选定的 provider 不支持识图（modality={chosen_modality}）。\n"
                "  · fixed 模式：换一个 modality=vision/multimodal 的 provider；或\n"
                "  · auto 模式：确认你已配置至少一条 modality=vision/multimodal 的 provider"
            )
            await event.edit(tip)
            return
        # 收集源消息（replied + self + 它们各自相册）
        try:
            image_msgs = await collect_image_sources(client, replied, self_msg)
        except Exception as e:  # noqa: BLE001
            await event.edit(f"✗ 图片预处理失败：{type(e).__name__}: {str(e)[:80]}")
            return
        # 逐条下载——任一失败就报清楚（不静默丢图）
        for src_msg in image_msgs:
            try:
                img_data = await download_image_bytes(client, src_msg)
            except ValueError as ve:
                # 用户层错误：撤回 / 超限——直接展示
                await event.edit(f"✗ {ve}")
                return
            except Exception as e:  # noqa: BLE001
                await event.edit(
                    f"✗ 图片下载失败：{type(e).__name__}: {str(e)[:80]}"
                )
                return
            image_bytes_list.append(img_data)
        log.warning(
            "[ai-debug] downloaded %d image(s) total %d bytes for provider=%s modality=%s",
            len(image_bytes_list), sum(len(b) for b in image_bytes_list),
            provider_dict.get("name"), chosen_modality,
        )

    # ── 音频数据：先 STT 转写为文字，再走标准 chat 流程 ─────────
    # 只在 provider modality∈{audio, multimodal} 时尝试；其它情况就拒，避免占位符瞎答。
    transcribed_text: str | None = None
    if has_any_audio and not has_any_image:
        # 含图时优先走 vision；同时含图含音的边角不在 V1 范围
        if not provider_supports_audio:
            tip = (
                f"✗ 消息含音频，但当前选定的 provider 不支持转写（modality={chosen_modality}）。\n"
                "  · fixed 模式：换一个 modality=audio/multimodal 的 provider；或\n"
                "  · auto 模式：确认你已配置至少一条 modality=audio/multimodal 的 provider"
            )
            await event.edit(tip)
            return
        audio_src = replied if has_replied_audio else self_msg
        try:
            audio_data = await download_audio_bytes(client, audio_src)
        except ValueError as ve:
            await event.edit(f"✗ {ve}")
            return
        except Exception as e:  # noqa: BLE001
            await event.edit(f"✗ 音频下载失败：{type(e).__name__}: {str(e)[:80]}")
            return
        log.warning(
            "[ai-debug] downloaded audio %d bytes for STT, provider=%s",
            len(audio_data), provider_dict.get("name"),
        )

    # ── 系统提示：基础值 + 反幻觉硬约束 ─────────────────────────
    # 永远附加，无视用户配置——不能让用户改成"请发挥想象"
    base_system = cfg.get("system_prompt") or "你是简洁有用的中文助手。回答控制在 100 字内。"
    _ANTI_HALLUCINATION = (
        "\n\n[严格规则]\n"
        "1. 当且仅当 user 输入包含真实图像数据时，才描述图像。\n"
        "2. 如果 user 输入只有 [图片] / 📷 等占位符而无真实图像数据，"
        "必须直接回答\"未收到图像数据，无法识别\"，绝对禁止臆测、编造或推断图像内容。\n"
        "3. 同样禁止仅凭 user 提问中出现的关键词（如\"这是 X 的封面\"）就肯定它是 X。"
    )
    system = base_system + _ANTI_HALLUCINATION
    max_tokens = int(cfg.get("max_tokens") or 512)
    
    # 决策 override_model 优先级：
    #   1. inline @name:model 显式指定 → 用该 model
    #   2. inline @name（未指定 model）→ 清空 override，让 build_client 用 provider.default_model
    #   3. 都没 inline override → 用模板配置的 model（可能为 None）
    if inline_model_override:
        # 情况 1：用户显式写了 @name:model
        override_model = inline_model_override
    elif inline_provider_override is not None:
        # 情况 2：用户只写了 @name，没写 :model
        # 必须清空 override_model，否则会错误地用模板里配的 model（那是给原 provider 用的）
        override_model = None
    else:
        # 情况 3：没有 inline override，按模板配置走
        override_model = cfg.get("model")

    # 占位回显，避免用户以为没反应（注意：edit 失败也要继续，非致命）
    # 一律简化为 "思考中..."；具体路由决策最终在 footer 的 {routing_note} 里展示
    try:
        await event.edit("思考中...")
    except Exception:  # noqa: BLE001
        pass

    # build_client 在内部解密 api_key；导入时点放函数内，避免循环依赖
    from ..db.models.command import LLMProvider as LLMProviderModel
    from ..services.llm_client import LLMError, build_client

    # 用一个 in-memory dataclass-like 对象传给 build_client 即可（属性访问相同）
    # 直接构造一个临时 ORM 对象（不绑定 session）保证字段一致
    fake_row = LLMProviderModel(
        id=int(chosen_provider_id),
        name=str(provider_dict.get("name", "")),
        provider=str(provider_dict.get("provider", "")),
        api_key_enc=provider_dict.get("api_key_enc"),
        base_url=provider_dict.get("base_url"),
        default_model=str(provider_dict.get("default_model", "")),
        api_format=provider_dict.get("api_format"),
    )

    try:
        llm = build_client(
            fake_row,
            override_model=override_model,
            proxy_url=provider_dict.get("proxy_url"),
        )
    except Exception as e:  # noqa: BLE001
        await event.edit(f"✗ AI 客户端构造失败：{type(e).__name__}: {str(e)[:120]}")
        return

    # ── STT：先把音频转写为文字，再走标准 chat 流程 ──────────
    # ``transcribe_model`` 由模板配（缺省 ``whisper-1``）——必须与 chat 模型分开，因为
    # 在 OpenAI / 兼容反代上 STT 是独立 model（``whisper-1`` / ``whisper-large`` 等）。
    if has_any_audio and not has_any_image:
        stt_model = str(cfg.get("transcribe_model") or "whisper-1").strip()
        try:
            transcribed_text = await llm.transcribe(audio_data, model=stt_model)
        except NotImplementedError:
            await event.edit(
                "✗ 当前 provider 暂不支持语音转写（仅 OpenAI 兼容 /audio/transcriptions）"
            )
            return
        except LLMError as e:
            await event.edit(f"✗ STT 调用失败：{e}")
            return
        except Exception as e:  # noqa: BLE001
            await event.edit(f"✗ STT 调用失败：{type(e).__name__}: {str(e)[:120]}")
            return
        log.warning("[ai-debug] STT got %d chars from %s", len(transcribed_text or ""), stt_model)

    # ── 拼 user prompt ─────────────────────────────────────────
    # 注意：当我们已经把图片字节单独传给 LLM 时，``replied_text`` 里的"📷 [图片]"占位符
    # 就**不要**再往 prompt 里塞了——否则模型会把占位符当成"用户在问一个看不见的图"
    # 反而触发"我看不到这张图，请描述"那种回答，反幻觉本意是想避免的恰恰这种。
    quoted_for_prompt = replied_text
    if image_bytes_list and replied_text is not None:
        # 用户没单独打字时 replied.text 是空，``replied_text`` 来自占位符 "📷 [图片]"——
        # 这种情况下从 prompt 里去掉，让模型自然把图片当作 user 输入的一部分回答
        original_text = (replied.text or replied.message or "") if replied is not None else ""
        if not original_text:
            quoted_for_prompt = None  # 占位符，不喂给模型
    # 转写文本同理：从 prompt 里替换占位符"🎤 [语音]"为真实转写
    if transcribed_text and replied_text is not None:
        original_text = (replied.text or replied.message or "") if replied is not None else ""
        if not original_text:
            # 把"🎤 [语音]"占位符替换为带[转写]标签的真文本
            quoted_for_prompt = f"[语音转写]\n{transcribed_text}"
    elif transcribed_text and replied_text is None:
        # self-msg 含语音、replied 为空——把转写直接塞进 prompt
        quoted_for_prompt = f"[语音转写]\n{transcribed_text}"

    if quote and quoted_for_prompt:
        user_msg = f"[原文]\n{quoted_for_prompt}\n\n[问题]\n{user_q or '解释/总结'}"
    else:
        if image_bytes_list:
            user_msg = user_q or (
                "请分别描述每张图。" if len(image_bytes_list) > 1 else "请描述这张图。"
            )
        elif transcribed_text:
            user_msg = user_q or f"[语音转写]\n{transcribed_text}"
        else:
            user_msg = user_q or "请简要总结你能想到的内容"

    try:
        result = await llm.complete(
            system,
            user_msg,
            max_tokens=max_tokens,
            images=image_bytes_list or None,
        )
    except LLMError as e:
        # message 已在 LLMError 内脱敏
        await event.edit(f"✗ AI 调用失败：{e}")
        return
    except Exception as e:  # noqa: BLE001
        await event.edit(f"✗ AI 调用失败：{type(e).__name__}: {str(e)[:120]}")
        return

    # ── 用 output_template 渲染最终消息 ─────────────────────────
    # 默认走 HTML：Telethon 1.36 的 sanitize_parse_mode 不接受 'markdownv2' 字符串
    # （会抛 ValueError），所以改用 HTML——telethon 内置全功能支持，包括
    # <blockquote expandable> 折叠引用块。
    # 老配置里 output_format='markdownv2' 自动当 'html' 处理（容错）。
    from ..services.llm_format import DEFAULT_TEMPLATE, render_output

    template = cfg.get("output_template") or DEFAULT_TEMPLATE
    raw_format = (cfg.get("output_format") or "html").lower()
    # 老数据兼容：markdownv2 → 当 html
    output_format = "html" if raw_format == "markdownv2" else raw_format
    escape_values = bool(cfg.get("escape_values", True))
    # 发送方式：edit = 原地编辑命令消息（默认，保留 reply 链）；
    # send_new = 删掉命令再发一条新消息（不带 reply_to）——避免在被回复方那里留下"你回复了我"的痕迹
    send_mode = str(cfg.get("send_mode") or "edit").lower()
    # send_new 自带图守卫：命令消息**自身**含图（caption 触发模式）时走 send_new
    # 会把图也删掉、聊天记录里图就没了，体验差。这种情况降级到 edit——把图保留在
    # 原消息上，caption 改写为 AI 回答。用户配置不变，仅本次单回合降级。
    self_msg_has_image = message_has_image(self_msg)
    if send_mode == "send_new" and self_msg_has_image:
        log.warning(
            "[ai-debug] downgrading send_mode send_new -> edit (self-msg has image; "
            "send_new would delete the photo)"
        )
        send_mode = "edit"

    render_ctx = {
        "answer": result.text or "",
        "question": user_q,
        "quoted": replied_text or "",
        "model": result.model or "",
        "provider": provider_dict.get("name", ""),
        "provider_kind": provider_dict.get("provider", ""),
        "in_tokens": result.input_tokens,
        "out_tokens": result.output_tokens,
        "total_tokens": result.input_tokens + result.output_tokens,
        "routing_note": (routing_note or "").replace("auto · ", ""),  # 去掉前缀让模板自己加
    }

    # 转义模式：html 走 HTML 转义；plain / markdown_v1 不转义；老 mdv2 也不进这里（已映射到 html）
    if escape_values and output_format == "html":
        escape_format: str | None = "html"
    else:
        escape_format = None

    body = render_output(template, render_ctx, escape_format=escape_format)

    # parse_mode：telethon 1.36 sanitize_parse_mode 接受 md/markdown/htm/html
    # 我们这里用 'html' / 'md' / None（plain）
    parse_mode_arg: str | None
    if output_format == "html":
        parse_mode_arg = "html"
    elif output_format in ("markdown", "markdown_v1", "md"):
        parse_mode_arg = "md"
    else:
        parse_mode_arg = None  # plain

    if send_mode == "send_new":
        # 删命令 + 发新消息（不附 reply_to）
        # 顺序：先发新消息，确保用户看到回答；再删命令——倒过来万一发失败，命令也没了，体验差
        try:
            await client.send_message(
                event.chat_id, body, parse_mode=parse_mode_arg
            )
        except Exception as e:  # noqa: BLE001
            # 发送失败时退化为纯文本再试；都失败就把错误编辑回原命令消息（不删）
            try:
                await client.send_message(event.chat_id, body)
            except Exception:
                try:
                    await event.edit(
                        f"{result.text}\n\n— {result.model} · in {result.input_tokens} / out {result.output_tokens}\n\n"
                        f"⚠ 发送异常：{type(e).__name__}"
                    )
                except Exception:
                    pass
                return
        # 发送成功才删命令
        try:
            await event.delete()
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        await event.edit(body, parse_mode=parse_mode_arg)
    except Exception as e:  # noqa: BLE001
        # 解析失败时（用户模板有未闭合 HTML 标签 / 未转义的特殊字符）退化为纯文本
        # 避免命令彻底失败，让用户至少能看到答案
        try:
            await event.edit(body)
        except Exception:
            # 实在不行就最简化版，至少把答案露出来
            try:
                await event.edit(
                    f"{result.text}\n\n— {result.model} · in {result.input_tokens} / out {result.output_tokens}\n\n"
                    f"⚠ 模板渲染异常：{type(e).__name__}",
                )
            except Exception:
                pass


def make_command_handler(client: TelegramClient, account_id: int, prefix: str | None = None):
    """创建并注册 TG 命令派发 handler。

    监听 ``outgoing=True`` 即只对本人发送的消息生效，避免误触发其他用户的同前缀消息。

    前缀热加载：handler 每次拦截消息时**从 ctx 读 prefix**，不再用闭包里固定 pattern。
    系统设置改前缀 → 主进程广播 IPC ``reload_global`` → runtime 重拉 ctx → 下一条消息立刻按
    新前缀匹配。``prefix`` 参数仅作"启动期默认"，正常运行靠 ctx 动态。
    """
    fallback_prefix = prefix or settings.command_prefix or ","

    @client.on(events.NewMessage(outgoing=True))
    async def _h(event):
        text = event.raw_text or ""
        
        # 先尝试 sudo_prefix 匹配（sudo 模式）
        sudo_p = (_ctx.sudo_prefix if _ctx else "") or "."
        pattern_sudo = re.compile(rf"^{re.escape(sudo_p)}(\w+)(?:\s+(.*))?$", re.S)
        m = pattern_sudo.match(text)
        use_sudo = False
        if m:
            use_sudo = True
            cmd = m.group(1)
            args_raw = (m.group(2) or "").strip()
        else:
            # 再尝试 command_prefix 匹配（普通模式）
            p = (_ctx.command_prefix if _ctx else "") or fallback_prefix
            pattern = re.compile(rf"^{re.escape(p)}(\w+)(?:\s+(.*))?$", re.S)
            m = pattern.match(text)
            if not m:
                return
            cmd = m.group(1)
            args_raw = (m.group(2) or "").strip()
        
        args = args_raw.split() if args_raw else []
        
        # 如果是 sudo 模式，检查权限
        if use_sudo:
            allowed, error_msg = await _check_sudo_permission(event, cmd, account_id)
            if not allowed:
                await event.edit(f"✗ Sudo 权限拒绝：{error_msg}")
                return

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
            await event.edit(f"未知命令：{cmd}（{p}help 查看可用列表）")
        except Exception:
            pass

    return _h
