"""内置插件：自动回复（PRD §C）。

支持能力：
  - 关键词匹配（默认）/ 正则匹配（``match_type=regex``）
  - 大小写敏感开关（``case_sensitive``）
  - 作用范围 ``scope``：``all`` | ``private`` | ``all_groups`` | ``groups``（结合 ``groups`` 列表）
  - 白 / 黑名单（``whitelist_chats`` / ``blacklist_chats``，以 chat_id 为单位）
  - 每规则、每会话独立冷却（Redis SETEX）
  - 模板变量 ``{sender}`` / ``{chat}`` / ``{text}``
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
      "cooldown_seconds": 30,
      "whitelist_chats": [...],
      "blacklist_chats": [...],
      "case_sensitive": false
    }
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from telethon import events

# 模块化重构后改用绝对 import：第三方插件解压到 data/plugins/installed/{key}/
# 时也只能走绝对 import，因此 builtin 同样统一用绝对路径以保持一致性。
from app.db.models.feature import FEATURE_AUTO_REPLY
from app.worker.command import should_allow_auto_command_text
from app.worker.plugins.base import Plugin, PluginContext, register
from app.worker.ratelimit.humanize import simulate_read, simulate_typing


@register
class AutoReplyPlugin(Plugin):
    """自动回复插件实现。"""

    key = FEATURE_AUTO_REPLY
    display_name = "自动回复"
    owner_only = False

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
            if not _match(cfg, text):
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
            # 4) 冷却（Redis SETEX）
            cool_key = f"ar:cool:{ctx.account_id}:{rule.id}:{chat_id}"
            try:
                if await ctx.redis.get(cool_key):
                    if ctx.log is not None:
                        await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 在冷却中")
                    continue
                cooldown = int(cfg.get("cooldown_seconds", 30) or 0)
                if cooldown > 0:
                    await ctx.redis.set(cool_key, "1", ex=cooldown)
            except Exception:
                # redis 不可用时不阻塞业务（可能本地 fakeredis 测试）；继续走风控
                pass

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
            text_out = _render(cfg.get("reply", ""), sender, chat, text)
            if not text_out:
                # 无内容直接 return：也算命中并消耗冷却
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

            # 8) 真正发送 + Telegram 异常回灌 engine
            #    reply_to 默认 True：以"引用"形式回复触发消息（视觉上挂在那条消息下方）；
            #    cfg.reply_to=False 时退化成普通新消息（event.respond）
            reply_to_msg = bool(cfg.get("reply_to", True))
            try:
                if reply_to_msg:
                    await event.reply(text_out)
                else:
                    await event.respond(text_out)
                if ctx.log is not None:
                    await ctx.log(
                        "info",
                        f"auto_reply 命中规则 #{rule.id} (reply_to={reply_to_msg})",
                        rule_id=rule.id,
                    )
            except Exception as exc:  # noqa: BLE001
                # 这里手动包装：因为我们没用 @rate_limited 装饰器
                await _handle_send_exception(ctx, action, chat_id, exc)
            return  # 命中一条即止


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
    """按 ``match_type`` (后端原名) 或 ``match`` (前端名) 做关键词或正则匹配。

    在普通字面比对失败时，会做一层 NFKC 归一化 + 去零宽字符 + strip 后再试一次，
    避免 Telegram 消息里夹零宽空格 / 全角数字 / BOM 等导致 "肉眼一样但码点不同" 的不命中。
    """
    patterns = cfg.get("patterns") or []
    if not patterns:
        return False
    case = bool(cfg.get("case_sensitive", False))
    mtype = cfg.get("match_type") or cfg.get("match") or "keyword"

    if mtype == "regex":
        flags = 0 if case else re.IGNORECASE
        for p in patterns:
            try:
                if re.search(p, text, flags):
                    return True
                # 归一化兜底
                if re.search(_normalize(p, case), _normalize(text, case), flags):
                    return True
            except re.error:
                continue
        return False

    # 默认走关键词包含匹配
    src = text if case else text.lower()
    if any((p if case else str(p).lower()) in src for p in patterns):
        return True
    # 归一化兜底
    src_n = _normalize(text, case)
    return any(_normalize(str(p), case) in src_n for p in patterns)


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


def _render(template: str, sender: Any, chat: Any, text: str) -> str:
    """简单模板渲染，仅支持 ``{sender}`` / ``{chat}`` / ``{text}``。

    sender / chat 为 None 时回退为空字符串，避免抛 AttributeError。
    """
    sender_name = ""
    if sender is not None:
        sender_name = (
            getattr(sender, "first_name", None)
            or getattr(sender, "username", None)
            or str(getattr(sender, "id", "") or "")
        )
    chat_name = ""
    if chat is not None:
        chat_name = (
            getattr(chat, "title", None)
            or getattr(chat, "first_name", None)
            or str(getattr(chat, "id", "") or "")
        )
    return (
        (template or "")
        .replace("{sender}", str(sender_name))
        .replace("{chat}", str(chat_name))
        .replace("{text}", text or "")
    )


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
    if not _match(cfg, text):
        return False, None
    rendered = _render(cfg.get("reply", ""), None, None, text)
    return True, rendered


__all__ = [
    "AutoReplyPlugin",
    "_dry_run_match",
    "_match",
    "_render",
    "_scope_ok",
]
