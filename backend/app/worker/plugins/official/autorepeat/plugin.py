"""自动复读插件 — 当群组中多名用户在指定时间内发送相同内容时自动复读。

规则驱动设计：
  - 每条 rule 代表一个群组的复读配置
  - rule.config: { target_chat_id, time_window, min_users }
  - on_message 遍历 ctx.rules（按 priority 倒序），找到 target_chat_id 匹配的规则
  - 找到匹配规则后，用该规则的 time_window / min_users 判断是否触发复读

复读逻辑：
  1. 在内存中维护最近消息记录（_recent_messages: chat_id -> [{userId, text, time}]）
  2. 当同一文本在 time_window 内由 ≥ min_users 位不同用户发送时，自动复读
  3. 同一群组内，相同内容每天只复读一次（UTC+8 0点重置）
  4. 忽略匿名消息、非文本消息、自己发送的消息、机器人消息

每日去重（Redis）：
  - autorepeat:daily:{account_id}:{chat_id}  SET，TTL 24h
  - 无 Redis 时回退内存 set（重启后丢失）

命令支持（保留，方便快捷操作）：
  - ,autorepeat on/off [标识符]
  - ,autorepeat list
  - ,autorepeat set [时间] [人数]  — 修改已有规则配置

来源：TeleBox_Plugins/autorepeat → 适配 TelePilot 插件框架
"""

from __future__ import annotations

import re
import time
from typing import Any

from telethon import utils as tl_utils
from telethon.tl.types import Channel, Chat, User

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register

# ─── 工具函数 ───────────────────────────────────────────


def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _redis_str(v) -> str:
    """Redis 返回值统一转 str。"""
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


def _is_group_chat(event) -> bool:
    """判断事件是否来自群组（含超级群组/megagroup）。"""
    if event.is_group:
        return True
    chat = getattr(event, "chat", None) or getattr(event, "_chat", None)
    if isinstance(chat, Channel) and getattr(chat, "megagroup", False):
        return True
    return False


def _content_key(text: str) -> str:
    """生成内容指纹：短文本用全文，长文本用前50字符+总长度。"""
    return text if len(text) <= 50 else text[:50] + str(len(text))


# ─── Redis 键名 ─────────────────────────────────────────

_RK_DAILY = "autorepeat:daily:{aid}:{cid}"  # SET, TTL 24h


# ─── 帮助文本 ───────────────────────────────────────────

HELP_TEXT = """<b>自动复读插件使用说明</b>

<b>配置方式：</b>
通过前端「账号 → 自动复读」页面添加规则，每条规则对应一个群组。

<b>指令列表（快捷操作）：</b>
<code>{prefix}autorepeat on [群组ID / @群组名]</code> - 添加规则开启群组
<code>{prefix}autorepeat off [群组ID / @群组名]</code> - 删除规则关闭群组
<code>{prefix}autorepeat list</code> - 查看已开启的群组
<code>{prefix}autorepeat</code> - 查看当前群组状态

<b>复读规则：</b>
• <b>触发条件</b>：指定时间内有指定人数的不同用户发送完全相同的内容
• <b>每日限制</b>：同一群组内，相同内容每天只会自动复读一次 (UTC+8 0点重置)
• <b>忽略规则</b>：匿名消息、非文本消息、自己发送的消息、机器人消息会被忽略"""


def _help_text() -> str:
    return HELP_TEXT.format(prefix=_html_escape(current_command_prefix()))


# ─── 插件主类 ───────────────────────────────────────────


@register
class AutoRepeatPlugin(Plugin):
    key = "autorepeat"
    display_name = "自动复读"
    description = "当群组中多名用户在指定时间内发送相同内容时自动复读。"
    message_channels = {"incoming"}
    owner_only = False

    # 内存运行态（每账号实例独立）
    _recent_messages: dict[int, list[dict]]  # chat_id -> [{userId, text, time}]
    _daily_fallback: dict[int, set[str]]     # chat_id -> content_key set（无 Redis 时的回退）
    _daily_fallback_day: int                  # UTC+8 天数
    _last_cleanup: float

    def __init__(self) -> None:
        super().__init__()
        self._recent_messages = {}
        self._daily_fallback = {}
        self._daily_fallback_day = 0
        self._last_cleanup = 0.0

    # ── 生命周期 ──────────────────────────────────────

    async def on_startup(self, ctx: PluginContext) -> None:
        self._recent_messages.clear()
        self._daily_fallback.clear()
        self._daily_fallback_day = 0
        self._last_cleanup = 0.0

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._recent_messages.clear()
        self._daily_fallback.clear()

    # ── 规则匹配 ──────────────────────────────────────

    @staticmethod
    def _find_matching_rule(
        rules: list[Any], chat_id: int
    ) -> tuple[Any, dict[str, Any]] | None:
        """从规则列表中找到 target_chat_id 匹配的规则，返回 (rule, config) 或 None。"""
        for rule in rules:
            if not rule.enabled:
                continue
            cfg: dict[str, Any] = rule.config or {}
            target = cfg.get("target_chat_id")
            if target is not None and int(target) == chat_id:
                return rule, cfg
        return None

    # ── 消息监听 ──────────────────────────────────────

    async def on_message(self, ctx: PluginContext, event) -> None:
        """监听群组消息，检测是否满足复读条件。"""
        if not ctx.rules:
            return

        # 仅处理群组消息
        if not _is_group_chat(event):
            return
        # 忽略自己发送的消息
        if event.out:
            return
        # 必须是文本消息
        text = event.text
        if not text:
            return
        chat_id = event.chat_id
        if chat_id is None:
            return

        # 查找匹配规则
        match = self._find_matching_rule(ctx.rules, chat_id)
        if match is None:
            return
        _rule, cfg = match

        # 忽略匿名发送者
        sender_id = event.sender_id
        if not sender_id:
            return
        # 忽略机器人
        try:
            sender = await event.get_sender()
            if isinstance(sender, User) and sender.bot:
                return
        except Exception:
            pass
        # 忽略过旧消息（只处理实时消息，60秒阈值）
        msg_date = getattr(event.message, "date", None)
        if msg_date is not None:
            import datetime
            if isinstance(msg_date, datetime.datetime):
                msg_ts = msg_date.timestamp()
            else:
                try:
                    msg_ts = float(msg_date)
                except (TypeError, ValueError):
                    msg_ts = time.time()
            if time.time() - msg_ts > 60:
                return

        time_window = int(cfg.get("time_window", 300))
        min_users = int(cfg.get("min_users", 5))

        now = time.time()

        # 定期清理
        self._cleanup(now, time_window)

        # 添加到消息记录
        msgs = self._recent_messages.get(chat_id, [])
        msgs.append({"userId": sender_id, "text": text, "time": now})
        # 过滤过期消息
        msgs = [m for m in msgs if now - m["time"] <= time_window]
        self._recent_messages[chat_id] = msgs

        # 检查复读条件
        await self._try_repeat(ctx, chat_id, text, msgs, min_users)

    # ── 复读核心逻辑 ──────────────────────────────────

    async def _try_repeat(
        self,
        ctx: PluginContext,
        chat_id: int,
        text: str,
        msgs: list[dict],
        min_users: int,
    ) -> None:
        """检查复读条件并执行。"""
        # 统计发送相同内容的不同用户数
        senders: set[int] = set()
        for m in msgs:
            if m["text"] == text:
                senders.add(m["userId"])

        if len(senders) < min_users:
            return

        ckey = _content_key(text)

        # 检查每日历史
        if not await self._check_and_mark_daily(ctx, chat_id, ckey):
            return  # 今日已复读过

        # 执行复读
        client = ctx.client
        if client:
            try:
                await client.send_message(chat_id, text)
                if ctx.log:
                    await ctx.log(
                        "info", "autorepeat triggered", chat_id=chat_id, content_key=ckey
                    )
            except Exception as exc:
                if ctx.log:
                    await ctx.log("warning", "autorepeat send failed", error=str(exc))

    async def _check_and_mark_daily(
        self, ctx: PluginContext, chat_id: int, content_key: str
    ) -> bool:
        """检查每日去重并标记。返回 True 表示可以复读，False 表示今日已复读。"""
        if ctx.redis:
            daily_key = _RK_DAILY.format(aid=ctx.account_id, cid=chat_id)
            already = await ctx.redis.sismember(daily_key, content_key)
            if already:
                return False
            # 标记为已复读
            pipe = ctx.redis.pipeline()
            pipe.sadd(daily_key, content_key)
            pipe.expire(daily_key, 86400)  # 24h 自动过期
            await pipe.execute()
            return True

        # Redis 不可用时用内存回退
        now = time.time()
        day_key = int((now + 8 * 3600) // 86400)  # UTC+8 天数
        if day_key > self._daily_fallback_day:
            self._daily_fallback.clear()
            self._daily_fallback_day = day_key
        if chat_id not in self._daily_fallback:
            self._daily_fallback[chat_id] = set()
        if content_key in self._daily_fallback[chat_id]:
            return False
        self._daily_fallback[chat_id].add(content_key)
        return True

    # ── 内存清理 ──────────────────────────────────────

    def _cleanup(self, now: float, time_window: int) -> None:
        """每 60 秒清理一次过期消息记录。"""
        if now - self._last_cleanup < 60:
            return
        for gid in list(self._recent_messages):
            msgs = self._recent_messages[gid]
            valid = [m for m in msgs if now - m["time"] <= time_window]
            if valid:
                self._recent_messages[gid] = valid
            else:
                del self._recent_messages[gid]
        self._last_cleanup = now

    # ── 命令入口 ──────────────────────────────────────

    async def on_command(
        self, ctx: PluginContext, cmd: str, args: list[str], event
    ) -> bool:
        if cmd != "autorepeat":
            return False
        try:
            await self._dispatch_command(ctx, args, event)
        except Exception as exc:
            try:
                await event.edit(f"❌ 操作失败: {_html_escape(str(exc))}")
            except Exception:
                pass
        return True

    async def _dispatch_command(
        self, ctx: PluginContext, args: list[str], event
    ) -> None:
        action = args[0].lower() if args else ""

        if action == "on":
            identifier = args[1] if len(args) > 1 else None
            await self._cmd_add_rule(ctx, event, identifier)
        elif action == "off":
            identifier = args[1] if len(args) > 1 else None
            await self._cmd_remove_rule(ctx, event, identifier)
        elif action == "list":
            await self._cmd_list(ctx, event)
        else:
            # 无子命令 → 群组中显示状态，私聊显示帮助
            if _is_group_chat(event):
                await self._cmd_status(ctx, event)
            else:
                await event.edit(_help_text())

    # ── 子命令实现 ────────────────────────────────────

    async def _cmd_add_rule(
        self, ctx: PluginContext, event, identifier: str | None
    ) -> None:
        """通过命令添加规则（快捷操作，等价于前端新建规则）。"""
        client = ctx.client
        if not client:
            await event.edit("❌ 客户端未初始化")
            return

        result = await self._parse_group_identifier(client, event, identifier)
        if not result["success"]:
            await event.edit(result.get("error", "操作失败"))
            return

        chat_id = result["chat_id"]
        title = result.get("title", str(chat_id))

        # 检查是否已存在规则
        existing = self._find_matching_rule(ctx.rules, chat_id)
        if existing:
            await event.edit(f"⚠️ <b>{_html_escape(title)}</b> 已有规则，无需重复添加")
            return

        # 通过数据库创建规则
        try:
            from app.db.base import AsyncSessionLocal
            from app.db.models.rule import Rule

            async with AsyncSessionLocal() as db:
                rule = Rule(
                    account_id=ctx.account_id,
                    feature_key="autorepeat",
                    name=title,
                    enabled=True,
                    priority=100,
                    config={
                        "target_chat_id": chat_id,
                        "time_window": 300,
                        "min_users": 5,
                    },
                )
                db.add(rule)
                await db.commit()

            # 通知 worker 热更新
            await self._request_reload(ctx)
            await event.edit(f"✅ 已添加 <b>{_html_escape(title)}</b> 的自动复读规则")
        except Exception as exc:
            await event.edit(f"❌ 添加规则失败: {_html_escape(str(exc))}")

    async def _cmd_remove_rule(
        self, ctx: PluginContext, event, identifier: str | None
    ) -> None:
        """通过命令删除规则（快捷操作）。"""
        client = ctx.client
        if not client:
            await event.edit("❌ 客户端未初始化")
            return

        result = await self._parse_group_identifier(client, event, identifier)
        if not result["success"]:
            await event.edit(result.get("error", "操作失败"))
            return

        chat_id = result["chat_id"]
        title = result.get("title", str(chat_id))

        # 查找对应规则
        match = self._find_matching_rule(ctx.rules, chat_id)
        if not match:
            await event.edit(f"⚠️ <b>{_html_escape(title)}</b> 没有规则，无需删除")
            return

        rule, _ = match
        try:
            from app.db.base import AsyncSessionLocal
            from app.db.models.rule import Rule

            async with AsyncSessionLocal() as db:
                await db.delete(await db.get(Rule, rule.id))
                await db.commit()

            await self._request_reload(ctx)
            await event.edit(f"❌ 已删除 <b>{_html_escape(title)}</b> 的自动复读规则")
        except Exception as exc:
            await event.edit(f"❌ 删除规则失败: {_html_escape(str(exc))}")

    async def _cmd_list(self, ctx: PluginContext, event) -> None:
        """列出当前所有规则对应的群组。"""
        if not ctx.rules:
            await event.edit("📝 当前没有自动复读规则")
            return

        client = ctx.client
        lines: list[str] = []
        for rule in ctx.rules:
            if not rule.enabled:
                continue
            cfg = rule.config or {}
            chat_id = cfg.get("target_chat_id", "?")
            time_window = cfg.get("time_window", 300)
            min_users = cfg.get("min_users", 5)
            if client and chat_id != "?":
                try:
                    entity = await client.get_entity(int(chat_id))
                    group_title = _html_escape(getattr(entity, "title", str(chat_id)))
                    lines.append(
                        f"• <b>{group_title}</b> (<code>{chat_id}</code>) "
                        f"— {time_window}秒/{min_users}人"
                    )
                except Exception:
                    lines.append(f"• <code>{chat_id}</code> — {time_window}秒/{min_users}人")
            else:
                lines.append(f"• <code>{chat_id}</code> — {time_window}秒/{min_users}人")

        text = f"📝 <b>自动复读规则 ({len(lines)}):</b>\n\n" + "\n".join(lines)
        await event.edit(text)

    async def _cmd_status(self, ctx: PluginContext, event) -> None:
        """查看当前群组状态。"""
        chat_id = event.chat_id
        match = self._find_matching_rule(ctx.rules, chat_id)
        if match:
            _, cfg = match
            status = "✅ 已开启"
            time_window = cfg.get("time_window", 300)
            min_users = cfg.get("min_users", 5)
        else:
            status = "❌ 已关闭"
            time_window = 300
            min_users = 5

        try:
            title = _html_escape(getattr(event.chat, "title", str(chat_id)))
        except Exception:
            title = str(chat_id)

        await event.edit(
            f"🤖 <b>{title}</b>\n"
            f"群组ID: <code>{chat_id}</code>\n"
            f"状态: {status}\n"
            f"触发条件: {time_window}秒内{min_users}人"
        )

    # ── 请求热更新 ────────────────────────────────────

    async def _request_reload(self, ctx: PluginContext) -> None:
        """请求 worker 热更新配置。"""
        try:
            from app.worker.ipc import CMD_RELOAD_CONFIG, send_cmd

            await send_cmd(CMD_RELOAD_CONFIG, {"plugin_key": "autorepeat"})
        except Exception:
            pass  # IPC 不可用时静默忽略（本地开发 / 测试）

    # ── 群组标识解析 ──────────────────────────────────

    @staticmethod
    def _extract_username(identifier: str) -> str | None:
        """从各种格式提取用户名。"""
        patterns = [
            r"^https?://t\.me/([a-zA-Z0-9_]+)",
            r"^t\.me/([a-zA-Z0-9_]+)",
            r"^@([a-zA-Z0-9_]+)$",
            r"^([a-zA-Z0-9_]{5,})$",
        ]
        for pat in patterns:
            m = re.match(pat, identifier)
            if m:
                return m.group(1)
        return None

    async def _parse_group_identifier(
        self, client, event, identifier: str | None = None
    ) -> dict[str, Any]:
        """解析群组标识符，支持多种格式。"""
        try:
            # 1. 回复转发消息
            if event.reply_to:
                try:
                    reply_msg = await event.get_reply_message()
                    if reply_msg and reply_msg.forward:
                        fwd_chat = reply_msg.forward.chat
                        if fwd_chat:
                            if isinstance(fwd_chat, (Chat, Channel)) and (
                                isinstance(fwd_chat, Chat) or getattr(fwd_chat, "megagroup", False)
                            ):
                                chat_id = tl_utils.get_peer_id(fwd_chat)
                                return {
                                    "success": True,
                                    "chat_id": chat_id,
                                    "title": getattr(fwd_chat, "title", str(chat_id)),
                                }
                except Exception:
                    pass

            # 2. 无标识符 → 当前群组
            if not identifier:
                if _is_group_chat(event):
                    chat_id = event.chat_id
                    title = getattr(event.chat, "title", str(chat_id)) if event.chat else str(chat_id)
                    return {"success": True, "chat_id": chat_id, "title": title}
                return {
                    "success": False,
                    "error": (
                        "❌ 请提供群组标识符或在群组中使用此命令\n支持格式:\n"
                        "• 群组ID: <code>-1001234567890</code>\n"
                        "• 公开群组: <code>@groupname</code>\n"
                        "• Telegram链接: <code>https://t.me/groupname</code>"
                    ),
                }

            # 3. 群组 ID（负数）
            if identifier.startswith("-") and not identifier.startswith("@"):
                try:
                    chat_id = int(identifier)
                    entity = await client.get_entity(chat_id)
                    if isinstance(entity, (Chat, Channel)):
                        marked_id = tl_utils.get_peer_id(entity)
                        return {
                            "success": True,
                            "chat_id": marked_id,
                            "title": getattr(entity, "title", str(marked_id)),
                        }
                except Exception:
                    return {
                        "success": False,
                        "error": f"❌ 无法访问群组 {_html_escape(identifier)}",
                    }

            # 4. @用户名 或 Telegram 链接
            username = self._extract_username(identifier)
            if username:
                try:
                    entity = await client.get_entity(username)
                    if isinstance(entity, Chat) or (
                        isinstance(entity, Channel) and getattr(entity, "megagroup", False)
                    ):
                        marked_id = tl_utils.get_peer_id(entity)
                        return {
                            "success": True,
                            "chat_id": marked_id,
                            "title": getattr(entity, "title", username),
                        }
                    return {"success": False, "error": "❌ 这不是一个群组"}
                except Exception:
                    return {
                        "success": False,
                        "error": f"❌ 无法找到群组 {_html_escape(identifier)}",
                    }

            return {"success": False, "error": "❌ 无效的群组标识符"}

        except Exception as exc:
            return {"success": False, "error": f"❌ 解析失败: {_html_escape(str(exc))}"}


# ─── dry-run 支持 ──────────────────────────────────────


def _dry_run_match(
    cfg: dict[str, Any],
    text: str,
    chat_id: int | None = None,
) -> tuple[bool, str | None]:
    """供 API dry-run 调用：判断是否命中 + 描述。"""
    target = cfg.get("target_chat_id")
    time_window = int(cfg.get("time_window", 300))
    min_users = int(cfg.get("min_users", 5))

    if target is None:
        return False, "未设置 target_chat_id"

    if chat_id is not None and int(target) != chat_id:
        return False, f"chat_id 不匹配（规则: {target}，样本: {chat_id}）"

    return (
        True,
        f"[dry-run] 复读检测：当 {time_window}s 内 ≥{min_users} 个不同用户发送相同文本时触发复读。"
        f"同内容同群每天只触发一次。",
    )


PLUGIN_CLASS = AutoRepeatPlugin


__all__ = [
    "AutoRepeatPlugin",
    "PLUGIN_CLASS",
    "_dry_run_match",
]
