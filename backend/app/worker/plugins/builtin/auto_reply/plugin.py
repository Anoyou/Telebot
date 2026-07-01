"""内置插件：自动回复（PRD §C）。

支持能力：
  - 关键词匹配（默认）/ 正则匹配（``match_type=regex``）
  - 大小写敏感开关（``case_sensitive``）
  - 作用范围 ``scope``：``all`` | ``private`` | ``all_groups`` | ``groups``（结合 ``groups`` 列表）
  - 白 / 黑名单（``whitelist_chats`` / ``blacklist_chats``，以 chat_id 为单位）
  - 每规则、每会话独立冷却（Redis SETEX）
  - 可选每用户冷却与每用户每日次数限制
  - 模板变量 ``{sender}`` / ``{chat}`` / ``{text}`` / ``{prefix}``；正则规则额外支持 ``{1}`` / ``{name}`` / ``{1|默认值}``
  - 风控集成：发送前 ``engine.acquire`` 拿决策；FloodWait/PeerFlood/SlowMode 自动反馈到 engine
  - 拟人化：``simulate_read`` + ``simulate_typing``
  - 命中即止：所有 enabled rule 按 priority 倒序遍历，第一条命中即触发并 return

rule.config 形如：
    {
      "match_type": "keyword" | "regex",
      "patterns": ["hello", "hi"],
      "scope": "all" | "private" | "all_groups" | "groups",
      "groups": [123, 456],            // scope=groups 时使用
      "reply": "world {sender}",
      "cooldown_seconds": 30,           // 兼容 "2s" / "2m" / "2h" / "2d"
      "cooldown_scope": "chat" | "user",
      "daily_limit_per_user": 2,
      "whitelist_chats": [...],
      "blacklist_chats": [...],
      "case_sensitive": false
    }
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from telethon import events

# 模块化重构后改用绝对 import：第三方插件解压到 data/plugins/installed/{key}/
# 时也只能走绝对 import，因此 builtin 同样统一用绝对路径以保持一致性。
from app.worker.command import (
    current_command_prefix,
    dispatch_auto_command_text,
    should_allow_auto_command_text,
)
from app.worker.plugins.base import Plugin, PluginContext, public_entity_display_name, register
from app.worker.ratelimit.humanize import simulate_read, simulate_typing

FEATURE_AUTO_REPLY = "auto_reply"


@register
class AutoReplyPlugin(Plugin):
    """自动回复插件实现。"""

    key = FEATURE_AUTO_REPLY
    display_name = "自动回复"
    owner_only = False

    def __init__(self) -> None:
        super().__init__()
        self.commands = {"arcd": self._cmd_reset_cooldown}

    async def on_message(
        self, ctx: PluginContext, event: events.NewMessage.Event
    ) -> None:
        """对每条 incoming 消息按规则优先级匹配，命中第一条立刻回复并 return。"""
        # 没规则就直接退出，避免无谓的 redis / engine 调用
        if not ctx.rules:
            return

        text: str = event.raw_text or ""
        chat_id: int | None = event.chat_id

        # 调试：本插件本次拿到了多少条规则
        if ctx.log is not None:
            try:
                await ctx.log(
                    "info",
                    f"[auto_reply] 收到消息 chat_id={chat_id} text={text!r:.80} rules={len(ctx.rules)}",
                )
            except Exception:  # noqa: BLE001
                pass

        for rule in ctx.rules:
            cfg: dict[str, Any] = rule.config or {}
            # 1) 黑白名单
            if not _whitelist_ok(cfg, chat_id):
                if ctx.log is not None:
                    await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 跳过：白名单")
                continue
            if _in_blacklist(cfg, chat_id):
                if ctx.log is not None:
                    await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 跳过：黑名单")
                continue
            # 2) 作用范围
            if not _scope_ok(cfg, event):
                if ctx.log is not None:
                    await ctx.log(
                        "info",
                        f"[auto_reply] 规则 #{rule.id} 跳过：scope 不匹配 "
                        f"(scope={cfg.get('scope')!r} chat_id={chat_id} group_ids={cfg.get('group_ids') or cfg.get('groups')!r})",
                    )
                continue
            # 3) 模式匹配
            match_vars = _match_vars(cfg, text)
            if match_vars is None:
                if ctx.log is not None:
                    # 失败时打印 text + pattern 的 repr 与字节十六进制，揭示同形不同码点的情况
                    pats = cfg.get("patterns") or []
                    text_repr = repr(text)[:120]
                    text_hex = text.encode("utf-8")[:80].hex()
                    pat_dump = []
                    for p in pats[:5]:
                        pp = str(p)
                        pat_dump.append(
                            f"{pp!r:.60} hex={pp.encode('utf-8')[:60].hex()}"
                        )
                    await ctx.log(
                        "info",
                        f"[auto_reply] 规则 #{rule.id} 跳过：pattern 未命中 | "
                        f"text={text_repr} hex={text_hex} | patterns=[{' | '.join(pat_dump)}]",
                    )
                continue
            if ctx.log is not None:
                await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 命中，准备回复")
            sender_id = _event_sender_id(event)
            # 4) 冷却与每日限额：先判断，成功发送 / 成功命令后再记账，
            # 避免置顶失败、查询参数错误也消耗用户次数。
            usage = await _check_usage_limit(ctx, rule.id, chat_id, sender_id, cfg)
            if not usage.allowed:
                if ctx.log is not None:
                    reason = "冷却中" if usage.reason == "cooldown" else "已达到每日次数上限"
                    await ctx.log("info", f"[auto_reply] 规则 #{rule.id} {reason}")
                await self._send_usage_notice(ctx, event, rule, cfg, text, match_vars, usage)
                return

            try:
                # 5) 风控决策
                action = (
                    "send_message_group"
                    if (event.is_group or event.is_channel)
                    else "send_message_private"
                )
                decision = await ctx.engine.acquire(
                    ctx.account_id, action, peer_id=chat_id
                )
                if not decision.allowed:
                    if ctx.log is not None:
                        await ctx.log(
                            "info",
                            f"auto_reply 被风控丢弃: outcome={decision.outcome}",
                            rule_id=rule.id,
                        )
                    return
                if decision.wait_seconds and decision.wait_seconds > 0:
                    await asyncio.sleep(float(decision.wait_seconds))

                # 6) 拟人化（best effort，异常忽略）
                try:
                    chat_obj = await event.get_chat()
                    opts = _get_humanize_opts(ctx)
                    await simulate_read(ctx.client, chat_obj, opts)
                    await simulate_typing(ctx.client, chat_obj, opts)
                except Exception:
                    pass

                # 7) 模板渲染
                try:
                    sender = await event.get_sender()
                except Exception:
                    sender = None
                try:
                    chat = await event.get_chat()
                except Exception:
                    chat = None
                text_out = _render(cfg.get("reply", ""), sender, chat, text, match_vars)
                if not text_out:
                    # 无内容直接 return：不消耗冷却/次数。
                    return
                allowed, command_key = should_allow_auto_command_text(text_out)
                if not allowed:
                    if ctx.log is not None:
                        await ctx.log(
                            "info",
                            f"[auto_reply] 规则 #{rule.id} 跳过：命令不在白名单（{command_key}）",
                            rule_id=rule.id,
                            command=command_key,
                        )
                    return

                if command_key is not None:
                    command_event = _AutoReplyCommandEvent(event, text_out)
                    dispatched = await dispatch_auto_command_text(
                        ctx.client,
                        command_event,
                        text_out,
                        account_id=ctx.account_id,
                    )
                    if dispatched:
                        if not command_event.succeeded:
                            if ctx.log is not None:
                                await ctx.log(
                                    "info",
                                    f"[auto_reply] 规则 #{rule.id} 自动命令执行失败，不消耗冷却/次数（{command_key}）",
                                    rule_id=rule.id,
                                    command=command_key,
                                )
                            return
                        await _mark_usage(ctx, rule.id, chat_id, sender_id, cfg, usage)
                        success_notice = await self._render_success_usage_notice(
                            event, rule, cfg, text, match_vars, usage
                        )
                        if success_notice:
                            await command_event.append_notice(success_notice)
                        if ctx.log is not None:
                            await ctx.log(
                                "info",
                                f"[auto_reply] 规则 #{rule.id} 已触发自动命令（{command_key}）",
                                rule_id=rule.id,
                                command=command_key,
                            )
                        return

                # 8) 真正发送 + Telegram 异常回灌 engine
                #    reply_to 默认 True：以"引用"形式回复触发消息（视觉上挂在那条消息下方）；
                #    cfg.reply_to=False 时退化成普通新消息（event.respond）
                reply_to_msg = bool(cfg.get("reply_to", True))
                try:
                    if reply_to_msg:
                        await event.reply(text_out)
                    else:
                        await event.respond(text_out)
                    await _mark_usage(ctx, rule.id, chat_id, sender_id, cfg, usage)
                    if ctx.log is not None:
                        await ctx.log(
                            "info",
                            f"auto_reply 命中规则 #{rule.id} (reply_to={reply_to_msg})",
                            rule_id=rule.id,
                        )
                    await self._send_success_usage_notice(
                        ctx, event, rule, cfg, text, match_vars, usage, include_non_final=False
                    )
                except Exception as exc:  # noqa: BLE001
                    # 这里手动包装：因为我们没用 @rate_limited 装饰器
                    await _handle_send_exception(ctx, action, chat_id, exc)
                return  # 命中一条即止
            finally:
                await _release_usage_claim(ctx, usage)

    async def _send_usage_notice(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        rule: Any,
        cfg: dict[str, Any],
        trigger_text: str,
        match_vars: dict[str, str],
        usage: _UsageStatus,
    ) -> None:
        if usage.reason == "cooldown":
            if cfg.get("cooldown_notice_enabled") is False:
                return
            template = str(cfg.get("cooldown_message_template") or "")
            if not template:
                template = (
                    _DEFAULT_COOLDOWN_NOTICE_WITH_LIMIT
                    if usage.daily_limit > 0 and usage.count_today > 0
                    else _DEFAULT_COOLDOWN_NOTICE
                )
        elif usage.reason == "daily_limit":
            if cfg.get("daily_limit_notice_enabled") is False:
                return
            template = str(cfg.get("daily_limit_message_template") or _DEFAULT_DAILY_LIMIT_NOTICE)
        else:
            return
        await self._send_notice(ctx, event, rule, cfg, trigger_text, match_vars, usage, template)

    async def _send_success_usage_notice(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        rule: Any,
        cfg: dict[str, Any],
        trigger_text: str,
        match_vars: dict[str, str],
        usage: _UsageStatus,
        *,
        include_non_final: bool = True,
    ) -> None:
        text_out = await self._render_success_usage_notice(
            event, rule, cfg, trigger_text, match_vars, usage, include_non_final=include_non_final
        )
        if text_out:
            await self._send_notice_text(ctx, event, cfg, text_out)

    async def _render_success_usage_notice(
        self,
        event: events.NewMessage.Event,
        rule: Any,
        cfg: dict[str, Any],
        trigger_text: str,
        match_vars: dict[str, str],
        usage: _UsageStatus,
        *,
        include_non_final: bool = True,
    ) -> str | None:
        if usage.daily_limit <= 0 or cfg.get("daily_limit_notice_enabled") is False:
            return None
        if not include_non_final and not usage.final_use:
            return None
        if usage.final_use:
            template = str(
                cfg.get("daily_limit_final_message_template") or _DEFAULT_DAILY_LIMIT_FINAL_NOTICE
            )
        else:
            template = str(
                cfg.get("daily_limit_success_message_template") or _DEFAULT_DAILY_LIMIT_SUCCESS_NOTICE
            )
        return await self._render_notice_text(event, rule, cfg, trigger_text, match_vars, usage, template)

    async def _render_notice_text(
        self,
        event: events.NewMessage.Event,
        rule: Any,
        cfg: dict[str, Any],
        trigger_text: str,
        match_vars: dict[str, str],
        usage: _UsageStatus,
        template: str,
    ) -> str | None:
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
        try:
            chat = await event.get_chat()
        except Exception:
            chat = None
        text_out = _render(
            template,
            sender,
            chat,
            trigger_text,
            match_vars,
            extra_vars=_usage_template_vars(
                rule,
                cfg,
                sender,
                usage,
                sender_id=_event_sender_id(event),
            ),
        )
        if not text_out:
            return None
        return text_out

    async def _send_notice(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        rule: Any,
        cfg: dict[str, Any],
        trigger_text: str,
        match_vars: dict[str, str],
        usage: _UsageStatus,
        template: str,
    ) -> None:
        text_out = await self._render_notice_text(
            event, rule, cfg, trigger_text, match_vars, usage, template
        )
        if text_out:
            await self._send_notice_text(ctx, event, cfg, text_out)

    async def _send_notice_text(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        cfg: dict[str, Any],
        text_out: str,
    ) -> None:
        action = (
            "send_message_group"
            if (event.is_group or event.is_channel)
            else "send_message_private"
        )
        if ctx.engine is not None:
            decision = await ctx.engine.acquire(ctx.account_id, action, peer_id=event.chat_id)
            if not decision.allowed:
                return
            if decision.wait_seconds and decision.wait_seconds > 0:
                await asyncio.sleep(float(decision.wait_seconds))

        try:
            if bool(cfg.get("reply_to", True)):
                await event.reply(text_out)
            else:
                await event.respond(text_out)
        except Exception as exc:  # noqa: BLE001
            await _handle_send_exception(ctx, action, event.chat_id, exc)

    async def _cmd_reset_cooldown(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        del client, account_id
        target_id, rule_filter, error = await _parse_reset_command_target(event, args)
        if error:
            await event.edit(error)
            return
        if target_id is None:
            await event.edit(_reset_help_text())
            return

        reset_rules = _filter_reset_rules(ctx.rules, rule_filter)
        if not reset_rules:
            await event.edit("未找到要重置的自动回复规则。")
            return

        chat_id = getattr(event, "chat_id", None)
        keys: list[str] = []
        for rule in reset_rules:
            keys.append(_chat_cooldown_key(ctx.account_id, rule.id, chat_id))
            keys.append(_user_cooldown_key(ctx.account_id, rule.id, chat_id, target_id))
            keys.append(_daily_limit_key(ctx.account_id, rule.id, chat_id, target_id))
            keys.append(f"ar:pending:{ctx.account_id}:{rule.id}:chat:{chat_id or 0}")
            keys.append(f"ar:pending:{ctx.account_id}:{rule.id}:user:{chat_id or 0}:{target_id or 0}")
        deleted = await _redis_delete_keys(ctx.redis, keys)
        scope = f"规则 {rule_filter}" if rule_filter else "当前会话全部自动回复规则"
        await event.edit(
            f"已重置用户 {target_id} 在{scope}下的会话/用户冷却和今日次数。"
            f"清理键 {deleted}/{len(keys)} 个。"
        )


_DEFAULT_COOLDOWN_NOTICE = "{user} 当前规则还在冷却中，距离下次可用 CD 还剩 {remaining}。"
_DEFAULT_COOLDOWN_NOTICE_WITH_LIMIT = (
    "{user} 今日已成功{action} {count}/{limit} 次，"
    "距离下次可用 CD 还剩 {remaining}。"
)
_DEFAULT_DAILY_LIMIT_NOTICE = (
    "{user} 今日已成功{action} {count}/{limit} 次，当日无法再次使用{feature}，"
    "如需使用请联系管理员或明日再用。"
)
_DEFAULT_DAILY_LIMIT_SUCCESS_NOTICE = (
    "{user} 今日已成功{action} {count}/{limit} 次，"
    "距离下次可用 CD 还剩 {remaining}。"
)
_DEFAULT_DAILY_LIMIT_FINAL_NOTICE = (
    "{user} 今日已成功{action} {count}/{limit} 次，"
    "当日无法再次使用{feature}，如需使用请联系管理员或明日再用。"
)


@dataclass(frozen=True)
class _UsageStatus:
    allowed: bool
    reason: str | None = None
    count_today: int = 0
    daily_limit: int = 0
    remaining_seconds: int = 0
    cooldown_seconds: int = 0
    next_count: int = 0
    final_use: bool = False
    pending_key: str | None = None


class _AutoReplyCommandEvent:
    """把自动回复生成的命令文本伪装成可派发事件。

    命令执行里的 ``event.edit(...)`` 对真实 incoming 消息不可用；这里把它转换成
    对原会话的 respond/reply，让自动命令直接产出执行结果。
    """

    def __init__(self, source: events.NewMessage.Event, raw_text: str) -> None:
        self._source = source
        self.raw_text = raw_text
        self.client = getattr(source, "client", None)
        self.chat_id = getattr(source, "chat_id", None)
        self.is_private = getattr(source, "is_private", False)
        self.is_group = getattr(source, "is_group", False)
        self.is_channel = getattr(source, "is_channel", False)
        self.outgoing = True
        self.outputs: list[str] = []
        self._last_parse_mode: Any | None = None
        self._sent_message: Any | None = None

    def __getattr__(self, name: str):
        return getattr(self._source, name)

    @property
    def succeeded(self) -> bool:
        return bool(self.outputs) and not any(_command_output_is_failure(item) for item in self.outputs)

    async def edit(self, *args, **kwargs):
        self._remember_output(args, kwargs)
        if self._sent_message is not None:
            editor = getattr(self._sent_message, "edit", None)
            if callable(editor):
                return await editor(*args, **kwargs)
        return await self._send_first(args, kwargs, prefer_reply=False)

    async def respond(self, *args, **kwargs):
        self._remember_output(args, kwargs)
        if self._sent_message is not None:
            editor = getattr(self._sent_message, "edit", None)
            if callable(editor):
                return await editor(*args, **kwargs)
        return await self._send_first(args, kwargs, prefer_reply=False)

    async def reply(self, *args, **kwargs):
        self._remember_output(args, kwargs)
        if self._sent_message is not None:
            editor = getattr(self._sent_message, "edit", None)
            if callable(editor):
                return await editor(*args, **kwargs)
        return await self._send_first(args, kwargs, prefer_reply=True)

    async def append_notice(self, text: str) -> None:
        notice = str(text or "").strip()
        if not notice:
            return
        base = self.outputs[-1] if self.outputs else ""
        merged = f"{base.rstrip()}\n{notice}" if base.strip() else notice
        kwargs = {"parse_mode": self._last_parse_mode} if self._last_parse_mode else {}
        await self.edit(merged, **kwargs)

    async def _send_first(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        prefer_reply: bool,
    ):
        responder = (
            getattr(self._source, "reply", None)
            if prefer_reply
            else getattr(self._source, "respond", None)
        )
        fallback = (
            getattr(self._source, "respond", None)
            if prefer_reply
            else getattr(self._source, "reply", None)
        )
        responder = responder or fallback
        if responder is None:
            client = self.client
            if client is not None and self.chat_id is not None:
                sent = await client.send_message(self.chat_id, *args, **kwargs)
                self._remember_sent_message(sent)
                return sent
            return None
        sent = await responder(*args, **kwargs)
        self._remember_sent_message(sent)
        return sent

    def _remember_output(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if args:
            text = args[0]
        else:
            text = kwargs.get("message") or kwargs.get("text") or ""
        if text is not None:
            self.outputs.append(str(text))
        parse_mode = kwargs.get("parse_mode")
        if parse_mode:
            self._last_parse_mode = parse_mode

    def _remember_sent_message(self, sent: Any) -> None:
        if sent is not None and hasattr(sent, "edit"):
            self._sent_message = sent


def _command_output_is_failure(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return True
    if raw.startswith(("❌", "✗", "×", "用法：", "用法:", "未知命令")):
        return True
    failure_markers = (
        "失败",
        "缺少",
        "请先配置",
        "不可用",
        "没有种子 ID",
        "没有种子ID",
        "未提供种子 ID",
        "未提供种子ID",
        "冷却",
        "正在处理",
        "重复触发",
        "重复消耗",
        "已处于置顶状态",
        "不再处理",
    )
    return any(marker in raw for marker in failure_markers)


# ─────────────────────────────────────────────────────
# 工具：作用范围 / 黑白名单 / 匹配 / 模板渲染
# ─────────────────────────────────────────────────────
def _scope_ok(cfg: dict, event: Any) -> bool:
    """根据 ``scope`` 判断当前事件是否落在规则作用范围内。

    scope 支持（兼容前端命名）：
      - ``"all"``（默认）：任何会话
      - ``"private"``：仅私聊
      - ``"all_groups"`` 或 ``"group_all"``：所有群 / 频道
      - ``"groups"`` 或 ``"group_specific"``（配合 ``cfg.groups`` 或 ``cfg.group_ids``）：指定 chat_id
      - ``{"groups": [...]}``：dict 形式的等价写法
    """
    scope = cfg.get("scope", "all")
    if scope == "all":
        return True
    if scope == "private":
        return bool(event.is_private)
    if scope in ("all_groups", "group_all"):
        return bool(event.is_group or event.is_channel)
    # dict 形式：{"groups": [...]}
    if isinstance(scope, dict) and "groups" in scope:
        candidates = _coerce_int_list(scope.get("groups") or [])
        return _chat_id_in(event.chat_id, candidates)
    # 字符串 "groups" / "group_specific" + cfg.groups | cfg.group_ids
    if scope in ("groups", "group_specific"):
        candidates = _coerce_int_list(cfg.get("groups") or cfg.get("group_ids") or [])
        return _chat_id_in(event.chat_id, candidates)
    return True


def _coerce_int_list(raw: Any) -> list[int]:
    """前端表单里 chat_id 列表是 ``string[]``，比对前转 int；解析失败的项跳过。"""
    out: list[int] = []
    for item in raw or []:
        if isinstance(item, int):
            out.append(item)
            continue
        try:
            out.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return out


# Telethon 对 supergroup/channel 的 ``event.chat_id`` 是 ``-100xxxxxxxxxx`` 形式
# （13 位以上、负数）；用户在 t.me/c/<id> URL 里看到的是去掉前缀的纯数字；
# basic group 是 ``-xxxxxxxxxx`` 形式；private 是正数。
# 为了让用户填什么都能命中，把每个 id 展开成所有合理等价表示。
_CHANNEL_PREFIX = 1_000_000_000_000  # -100... 实际上是 -(1e12 + bare)


def _expand_chat_id(raw: int) -> set[int]:
    """把一个 chat id 展开成所有可能的等价表示。

    例：用户填 1234567890 → 也能匹配 -1001234567890 / -1234567890
       用户填 -1001234567890 → 同样展开到 1234567890 / -1234567890
    """
    out: set[int] = {raw}
    a = abs(raw)
    out.add(a)
    out.add(-a)
    if a > _CHANNEL_PREFIX:
        bare = a - _CHANNEL_PREFIX
        out.add(bare)
        out.add(-bare)
    else:
        out.add(-(_CHANNEL_PREFIX + a))
    return out


def _chat_id_in(target: int | None, candidates: list[int]) -> bool:
    """鲁棒匹配：candidates 中任一 id 的等价集若包含 target 即命中。"""
    if target is None or not candidates:
        return False
    target_set = _expand_chat_id(int(target))
    for c in candidates:
        if target_set & _expand_chat_id(int(c)):
            return True
    return False


def _whitelist_ok(cfg: dict, chat_id: int | None) -> bool:
    """白名单非空时仅放行白名单 chat；为空表示不启用。

    兼容字段名 ``whitelist_chats``（后端原写法）和 ``whitelist``（前端写法）。
    """
    wl = _coerce_int_list(cfg.get("whitelist_chats") or cfg.get("whitelist") or [])
    if not wl:
        return True
    return chat_id in wl


def _in_blacklist(cfg: dict, chat_id: int | None) -> bool:
    """黑名单命中即拒。兼容 ``blacklist_chats`` / ``blacklist``。"""
    bl = _coerce_int_list(cfg.get("blacklist_chats") or cfg.get("blacklist") or [])
    return bool(bl) and chat_id in bl


def _match(cfg: dict, text: str) -> bool:
    return _match_vars(cfg, text) is not None


def _match_vars(cfg: dict, text: str) -> dict[str, str] | None:
    """按 ``match_type`` / ``match`` 做关键词或正则匹配，并返回可渲染捕获值。

    在普通字面比对失败时，会做一层 NFKC 归一化 + 去零宽字符 + strip 后再试一次，
    避免 Telegram 消息里夹零宽空格 / 全角数字 / BOM 等导致 "肉眼一样但码点不同" 的不命中。
    """
    patterns = cfg.get("patterns") or []
    if not patterns:
        return None
    case = bool(cfg.get("case_sensitive", False))
    mtype = cfg.get("match_type") or cfg.get("match") or "keyword"

    if mtype == "regex":
        flags = 0 if case else re.IGNORECASE
        for p in patterns:
            try:
                match = re.search(str(p), text, flags)
                if match:
                    return _regex_match_vars(match)
                # 归一化兜底
                match = re.search(_normalize(str(p), case), _normalize(text, case), flags)
                if match:
                    return _regex_match_vars(match)
            except re.error:
                continue
        return None

    if mtype == "template":
        flags = 0 if case else re.IGNORECASE
        for p in patterns:
            regex = _template_pattern_regex(str(p))
            if regex is None:
                continue
            try:
                match = re.fullmatch(regex, text.strip(), flags)
                if match:
                    return _regex_match_vars(match)
                match = re.fullmatch(regex, _normalize(text, case), flags)
                if match:
                    return _regex_match_vars(match)
            except re.error:
                continue
        return None

    # 默认走关键词包含匹配
    src = text if case else text.lower()
    if any((str(p) if case else str(p).lower()) in src for p in patterns):
        return {}
    # 归一化兜底
    src_n = _normalize(text, case)
    return {} if any(_normalize(str(p), case) in src_n for p in patterns) else None


def _regex_match_vars(match: re.Match[str]) -> dict[str, str]:
    values: dict[str, str] = {"0": match.group(0) or ""}
    for index, value in enumerate(match.groups(), start=1):
        values[str(index)] = value or ""
    for name, value in match.groupdict().items():
        values[name] = value or ""
    return values


_TEMPLATE_TOKEN_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)(\?)?\}$")
_TEMPLATE_BRACKET_TOKEN_RE = re.compile(r"^【([A-Za-z_][A-Za-z0-9_]*)(\?)?】$")
_TEMPLATE_SPEC_TOKEN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(数字|number|num|文本|text|任意|any)(\?)?$", re.IGNORECASE)


def _template_pattern_regex(pattern: str) -> str | None:
    tokens = pattern.strip().split()
    if not tokens:
        return None
    parts: list[str] = [r"\s*"]
    for index, token in enumerate(tokens):
        var_match = _TEMPLATE_TOKEN_RE.fullmatch(token)
        bracket_match = _TEMPLATE_BRACKET_TOKEN_RE.fullmatch(token)
        spec_match = _TEMPLATE_SPEC_TOKEN_RE.fullmatch(token)
        match = var_match or bracket_match
        if spec_match:
            name = spec_match.group(1)
            value_type = spec_match.group(2).lower()
            value_body = r"\d+" if value_type in {"数字", "number", "num"} else r"\S+"
            body = rf"{re.escape(name)}\s*=\s*(?P<{name}>{value_body})"
            optional = bool(spec_match.group(3))
        elif match:
            name = match.group(1)
            body = rf"(?P<{name}>\S+)"
            optional = bool(match.group(2))
        else:
            body = re.escape(token)
            optional = False
        if index == 0:
            parts.append(f"(?:{body})?" if optional else body)
            continue
        if optional:
            parts.append(rf"(?:\s+{body})?")
        else:
            parts.append(rf"\s+{body}")
    parts.append(r"\s*")
    return "".join(parts)


def _event_sender_id(event: Any) -> int | None:
    for attr in ("sender_id", "from_id", "user_id"):
        value = getattr(event, attr, None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            continue
    msg = getattr(event, "message", None)
    for attr in ("sender_id", "from_id", "user_id"):
        value = getattr(msg, attr, None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _user_cooldown_key(account_id: int, rule_id: int, chat_id: int | None, sender_id: int | None) -> str:
    return f"ar:cool:{account_id}:{rule_id}:user:{chat_id or 0}:{sender_id or 0}"


def _cooldown_key(account_id: int, rule_id: int, chat_id: int | None, sender_id: int | None, cfg: dict[str, Any]) -> str:
    scope = str(cfg.get("cooldown_scope") or "chat")
    if scope == "user":
        return _user_cooldown_key(account_id, rule_id, chat_id, sender_id)
    return f"ar:cool:{account_id}:{rule_id}:chat:{chat_id or 0}"


def _chat_cooldown_key(account_id: int, rule_id: int, chat_id: int | None) -> str:
    return f"ar:cool:{account_id}:{rule_id}:chat:{chat_id or 0}"


def _usage_pending_key(
    account_id: int,
    rule_id: int,
    chat_id: int | None,
    sender_id: int | None,
    cfg: dict[str, Any],
    cooldown_seconds: int,
) -> str:
    scope = str(cfg.get("cooldown_scope") or "chat")
    if cooldown_seconds > 0 and scope != "user":
        return f"ar:pending:{account_id}:{rule_id}:chat:{chat_id or 0}"
    return f"ar:pending:{account_id}:{rule_id}:user:{chat_id or 0}:{sender_id or 0}"


def _usage_pending_ttl(cooldown_seconds: int) -> int:
    return min(max(int(cooldown_seconds or 0), 30), 300)


def _parse_duration_seconds(value: Any, *, default: int = 0) -> int:
    if value is None:
        return max(0, int(default))
    if isinstance(value, int | float):
        return max(0, int(value))
    raw = str(value).strip()
    if not raw:
        return 0
    match = re.fullmatch(r"(\d+)\s*([A-Za-z\u4e00-\u9fff]*)", raw)
    if not match:
        return max(0, int(default))
    number = int(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "": 1,
        "s": 1,
        "sec": 1,
        "secs": 1,
        "second": 1,
        "seconds": 1,
        "秒": 1,
        "m": 60,
        "min": 60,
        "mins": 60,
        "minute": 60,
        "minutes": 60,
        "分": 60,
        "分钟": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "小时": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "天": 86400,
    }
    multiplier = multipliers.get(unit)
    if multiplier is None:
        return max(0, int(default))
    return number * multiplier


def _daily_limit_key(account_id: int, rule_id: int, chat_id: int | None, sender_id: int | None) -> str:
    day = datetime.now().strftime("%Y%m%d")
    return f"ar:quota:{account_id}:{rule_id}:user_day:{day}:{chat_id or 0}:{sender_id or 0}"


def _seconds_until_next_day() -> int:
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


async def _redis_increment_with_ttl(redis: Any, key: str, ttl: int) -> int:
    try:
        incr = getattr(redis, "incr", None)
        expire = getattr(redis, "expire", None)
        if callable(incr):
            value = int(await incr(key))
            if value == 1 and ttl > 0 and callable(expire):
                await expire(key, ttl)
            return value
    except Exception:
        pass
    raw = await redis.get(key)
    try:
        value = int(raw or 0) + 1
    except (TypeError, ValueError):
        value = 1
    await redis.set(key, str(value), ex=ttl)
    return value


async def _redis_set_if_absent(redis: Any, key: str, value: str, ttl: int) -> bool:
    set_fn = getattr(redis, "set", None)
    if callable(set_fn):
        try:
            return bool(await set_fn(key, value, ex=ttl, nx=True))
        except TypeError:
            pass
        except Exception:
            raise
    if await redis.get(key):
        return False
    await redis.set(key, value, ex=ttl)
    return True


async def _redis_get_int(redis: Any, key: str) -> int:
    raw = await redis.get(key)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def _redis_ttl_seconds(redis: Any, key: str) -> int:
    ttl_attr = getattr(redis, "ttl", None)
    try:
        if callable(ttl_attr):
            value = await ttl_attr(key)
        elif isinstance(ttl_attr, dict):
            value = ttl_attr.get(key, -1)
        else:
            return -1
        return int(value)
    except Exception:
        return -1


async def _redis_delete_keys(redis: Any, keys: list[str]) -> int:
    if not keys:
        return 0
    delete = getattr(redis, "delete", None)
    try:
        if callable(delete):
            return int(await delete(*keys))
    except Exception:
        pass
    deleted = 0
    kv = getattr(redis, "kv", None)
    ttl = getattr(redis, "ttl", None)
    for key in keys:
        if isinstance(kv, dict) and key in kv:
            kv.pop(key, None)
            deleted += 1
        if isinstance(ttl, dict):
            ttl.pop(key, None)
    return deleted


def _daily_limit_value(cfg: dict[str, Any]) -> int:
    try:
        return max(0, int(cfg.get("daily_limit_per_user") or 0))
    except (TypeError, ValueError):
        return 0


async def _check_usage_limit(
    ctx: PluginContext,
    rule_id: int,
    chat_id: int | None,
    sender_id: int | None,
    cfg: dict[str, Any],
) -> _UsageStatus:
    limit = _daily_limit_value(cfg)
    count_today = 0
    if limit > 0 and sender_id is not None:
        try:
            count_today = await _redis_get_int(
                ctx.redis,
                _daily_limit_key(ctx.account_id, rule_id, chat_id, sender_id),
            )
        except Exception:
            count_today = 0
        if count_today >= limit:
            return _UsageStatus(
                allowed=False,
                reason="daily_limit",
                count_today=limit,
                daily_limit=limit,
                next_count=limit,
            )

    cooldown = _parse_duration_seconds(cfg.get("cooldown_seconds", 30), default=30)
    cool_key = _cooldown_key(ctx.account_id, rule_id, chat_id, sender_id, cfg)
    try:
        ttl = await _redis_ttl_seconds(ctx.redis, cool_key)
        cool_value = await ctx.redis.get(cool_key)
    except Exception:
        return _UsageStatus(
            allowed=True,
            count_today=count_today,
            daily_limit=limit,
            cooldown_seconds=cooldown,
            next_count=count_today + (1 if limit > 0 and sender_id is not None else 0),
        )
    if cool_value:
        remaining = ttl if ttl > 0 else cooldown
        return _UsageStatus(
            allowed=False,
            reason="cooldown",
            count_today=count_today,
            daily_limit=limit,
            remaining_seconds=max(0, int(remaining)),
            cooldown_seconds=cooldown,
            next_count=count_today,
        )

    pending_key: str | None = None
    if cooldown > 0 or (limit > 0 and sender_id is not None):
        pending_key = _usage_pending_key(ctx.account_id, rule_id, chat_id, sender_id, cfg, cooldown)
        pending_ttl = _usage_pending_ttl(cooldown)
        try:
            claimed = await _redis_set_if_absent(ctx.redis, pending_key, "1", pending_ttl)
        except Exception:
            claimed = True
            pending_key = None
        if not claimed:
            pending_ttl_left = await _redis_ttl_seconds(ctx.redis, pending_key)
            remaining = pending_ttl_left if pending_ttl_left > 0 else pending_ttl
            return _UsageStatus(
                allowed=False,
                reason="cooldown",
                count_today=count_today,
                daily_limit=limit,
                remaining_seconds=max(0, int(remaining)),
                cooldown_seconds=cooldown,
                next_count=count_today,
            )

    next_count = count_today + (1 if limit > 0 and sender_id is not None else 0)
    return _UsageStatus(
        allowed=True,
        count_today=count_today,
        daily_limit=limit,
        cooldown_seconds=cooldown,
        next_count=next_count,
        final_use=limit > 0 and next_count >= limit,
        pending_key=pending_key,
    )


async def _mark_usage(
    ctx: PluginContext,
    rule_id: int,
    chat_id: int | None,
    sender_id: int | None,
    cfg: dict[str, Any],
    usage: _UsageStatus,
) -> None:
    try:
        if usage.cooldown_seconds > 0:
            await ctx.redis.set(
                _cooldown_key(ctx.account_id, rule_id, chat_id, sender_id, cfg),
                "1",
                ex=usage.cooldown_seconds,
            )
        if usage.daily_limit > 0 and sender_id is not None:
            await _redis_increment_with_ttl(
                ctx.redis,
                _daily_limit_key(ctx.account_id, rule_id, chat_id, sender_id),
                _seconds_until_next_day(),
            )
    except Exception:
        # Redis 不可用时不阻塞业务；等价于本轮不做限频记账。
        return


async def _release_usage_claim(ctx: PluginContext, usage: _UsageStatus) -> None:
    if not usage.pending_key:
        return
    try:
        await _redis_delete_keys(ctx.redis, [usage.pending_key])
    except Exception:
        return


async def _daily_limit_ok(
    ctx: PluginContext,
    rule_id: int,
    chat_id: int | None,
    sender_id: int | None,
    cfg: dict[str, Any],
) -> bool:
    try:
        limit = int(cfg.get("daily_limit_per_user") or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0 or sender_id is None:
        return True
    key = _daily_limit_key(ctx.account_id, rule_id, chat_id, sender_id)
    count = await _redis_increment_with_ttl(ctx.redis, key, _seconds_until_next_day())
    return count <= limit


# ── Unicode 归一化兜底 ────────────────────────────────────────
# Telegram 客户端有时会插入零宽 / 不可见控制字符，或者使用全角数字 / 兼容字符；
# NFKC 把"宽形式 / 兼容形式"归到标准 ASCII 区，再把已知不可见字符抹掉。
_INVISIBLE_CODES = (
    "​‌‍‎‏‪‫‬‭‮⁠﻿"
)
_INVISIBLE_TABLE = str.maketrans("", "", _INVISIBLE_CODES)


def _normalize(s: str, case_sensitive: bool = False) -> str:
    import unicodedata

    out = unicodedata.normalize("NFKC", s).translate(_INVISIBLE_TABLE).strip()
    return out if case_sensitive else out.lower()


def _render(
    template: str,
    sender: Any,
    chat: Any,
    text: str,
    match_vars: dict[str, str] | None = None,
    extra_vars: dict[str, Any] | None = None,
) -> str:
    """模板渲染，支持内置变量和正则捕获变量。

    sender / chat 为 None 时回退为空字符串，避免抛 AttributeError。
    """
    sender_name = ""
    if sender is not None:
        sender_name = public_entity_display_name(sender, default="")
    chat_name = ""
    if chat is not None:
        chat_name = public_entity_display_name(chat, default="")
    values = {
        **(match_vars or {}),
        "sender": str(sender_name),
        "chat": str(chat_name),
        "text": text or "",
        "prefix": current_command_prefix(fallback=","),
    }
    if extra_vars:
        values.update({str(k): str(v) for k, v in extra_vars.items()})

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        fallback = match.group(2)
        if key not in values:
            return fallback if fallback is not None else match.group(0)
        value = values.get(key)
        if fallback is not None:
            return value or fallback
        return value or ""

    return re.sub(r"\{([A-Za-z0-9_]+)(?:\|([^{}]*))?\}", replace, template or "")


def _sender_display(sender: Any, sender_id: int | None = None) -> str:
    return public_entity_display_name(sender, fallback_id=sender_id, default="", include_at=True)


def _rule_usage_labels(rule: Any, cfg: dict[str, Any]) -> tuple[str, str]:
    raw = str(
        cfg.get("usage_label")
        or cfg.get("usage_name")
        or getattr(rule, "name", "")
        or ""
    ).strip()
    if not raw:
        return "使用该功能", "该功能"
    feature = raw if raw.endswith("功能") else f"{raw}功能"
    return raw, feature


def _format_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds <= 0:
        return "不到 1 秒"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes}分钟")
    if not parts and secs:
        parts.append(f"{secs}秒")
    return "".join(parts[:2]) or "不到 1 秒"


def _usage_template_vars(
    rule: Any,
    cfg: dict[str, Any],
    sender: Any,
    usage: _UsageStatus,
    *,
    sender_id: int | None = None,
) -> dict[str, str]:
    action, feature = _rule_usage_labels(rule, cfg)
    count = usage.next_count if usage.allowed and usage.next_count else usage.count_today
    if usage.reason == "daily_limit":
        count = usage.daily_limit or usage.count_today
    remaining_seconds = usage.remaining_seconds
    if usage.allowed and remaining_seconds <= 0:
        remaining_seconds = usage.cooldown_seconds
    return {
        "user": _sender_display(sender, sender_id),
        "action": action,
        "feature": feature,
        "count": str(max(0, count)),
        "limit": str(max(0, usage.daily_limit)),
        "remaining": _format_remaining(remaining_seconds),
        "remaining_seconds": str(max(0, remaining_seconds)),
        "next_count": str(max(0, usage.next_count)),
    }


def _reset_help_text() -> str:
    prefix = current_command_prefix(fallback=",")
    return (
        "自动回复冷却重置命令：\n"
        f"{prefix}arcd\n"
        "  回复某个群友的消息发送，重置当前会话相关的会话/用户冷却和他的今日次数。\n"
        f"{prefix}arcd 123456789\n"
        "  按 Telegram 用户 ID 重置。\n"
        f"{prefix}arcd 123456789 规则ID\n"
        "  只重置某一条自动回复规则。"
    )


async def _parse_reset_command_target(
    event: Any,
    args: list[str],
) -> tuple[int | None, str | None, str | None]:
    if args and args[0].lower() in {"help", "-h", "--help"}:
        return None, None, _reset_help_text()
    target_id: int | None = None
    rule_filter: str | None = None
    rest = list(args)
    if rest:
        raw = rest.pop(0).strip()
        target_id = await _parse_user_identifier(event, raw)
        if target_id is None:
            return None, None, f"无法识别用户：{raw}\n\n{_reset_help_text()}"
    else:
        target_id = await _reply_sender_id(event)
    if rest:
        rule_filter = rest[0].strip()
    return target_id, rule_filter, None


async def _parse_user_identifier(event: Any, raw: str) -> int | None:
    token = raw.strip()
    if not token:
        return None
    if "=" in token:
        token = token.split("=", 1)[1].strip()
    if token.startswith("@"):
        client = getattr(event, "client", None)
        if client is not None:
            try:
                entity = await client.get_entity(token)
                value = getattr(entity, "id", None)
                return int(value) if value is not None else None
            except Exception:
                return None
    token = token.lstrip("@")
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


async def _reply_sender_id(event: Any) -> int | None:
    getter = getattr(event, "get_reply_message", None)
    if not callable(getter):
        return None
    try:
        msg = await getter()
    except Exception:
        return None
    for attr in ("sender_id", "from_id", "user_id"):
        value = getattr(msg, attr, None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _filter_reset_rules(rules: list[Any], rule_filter: str | None) -> list[Any]:
    if not rule_filter:
        return list(rules or [])
    out: list[Any] = []
    for rule in rules or []:
        if str(getattr(rule, "id", "")) == rule_filter:
            out.append(rule)
            continue
        if str(getattr(rule, "name", "")).strip() == rule_filter:
            out.append(rule)
    return out


def _get_humanize_opts(ctx: PluginContext):
    """从 engine 拿拟人化配置；兜底返回默认的 ``HumanizeOpts``。"""
    if ctx.engine is not None and getattr(ctx.engine, "humanize", None) is not None:
        return ctx.engine.humanize
    # 走到这里说明上下文异常，构造一个默认值避免崩溃
    from app.worker.ratelimit.humanize import HumanizeOpts as _Opts

    return _Opts()


async def _handle_send_exception(
    ctx: PluginContext, action: str, peer_id: int | None, exc: Exception
) -> None:
    """把 Telethon 发送异常映射回 engine 的回调；其它异常仅写日志。"""
    # 延迟 import：避免 telethon 缺失时 import 期失败
    try:
        from telethon.errors import (
            FloodWaitError,
            PeerFloodError,
            PhoneNumberFloodError,
            SlowModeWaitError,
        )
    except Exception:  # pragma: no cover - 极端环境兜底
        FloodWaitError = PeerFloodError = SlowModeWaitError = PhoneNumberFloodError = ()  # type: ignore[assignment]

    if isinstance(exc, FloodWaitError):
        await ctx.engine.on_flood_wait(action, exc)
    elif isinstance(exc, PeerFloodError):
        await ctx.engine.on_peer_flood("dm_stranger")
    elif isinstance(exc, SlowModeWaitError):
        await ctx.engine.on_slow_mode(action, exc, peer_id)
    elif isinstance(exc, PhoneNumberFloodError):
        await ctx.engine.on_phone_flood(action, exc)
    else:
        if ctx.log is not None:
            await ctx.log("error", f"auto_reply 发送失败: {type(exc).__name__}: {exc}")


# 暴露给 dry-run / 测试使用的内部工具
def _dry_run_match(
    cfg: dict,
    text: str,
    chat_type: str = "private",
    chat_id: int | None = None,
) -> tuple[bool, str | None]:
    """供 API ``dry-run`` 调用：仅做"是否命中 + 渲染"的纯函数判断。

    chat_id：仅在 chat_type 是 group/channel 且规则 scope=group_specific 时需要；
    未传时若规则 scope=group_specific 会用 ``group_ids`` 第一项当样本，让用户更容易看到命中。
    """
    if chat_id is None and cfg.get("scope") in ("groups", "group_specific"):
        gids = _coerce_int_list(cfg.get("groups") or cfg.get("group_ids") or [])
        if gids:
            chat_id = gids[0]

    class _FakeEvent:
        is_private = chat_type == "private"
        is_group = chat_type == "group"
        is_channel = chat_type == "channel"

    event = _FakeEvent()
    event.chat_id = chat_id if chat_id is not None else 0  # type: ignore[attr-defined]
    if not _scope_ok(cfg, event):
        return False, None
    match_vars = _match_vars(cfg, text)
    if match_vars is None:
        return False, None
    rendered = _render(cfg.get("reply", ""), None, None, text, match_vars)
    return True, rendered


__all__ = [
    "AutoReplyPlugin",
    "_dry_run_match",
    "_match",
    "_match_vars",
    "_parse_duration_seconds",
    "_render",
    "_scope_ok",
]
