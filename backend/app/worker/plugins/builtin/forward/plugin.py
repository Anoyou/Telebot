"""内置插件：消息转发（PRD §B）。

支持能力：
  - 四种源筛选：``all`` / ``peers``（chat_id 列表）/ ``keyword``（文本包含）/
    ``duplicate``（复读检测：统计不同用户发送相同文本的数量）
  - ``include_media`` 开关：False 时跳过含媒体的消息（仅文本）
  - 四种转发方式（``mode``）：
      * ``forward_native``  —— 原生转发，保留原作者署名（``message.forward_to``）
      * ``copy_text``       —— 仅复制文字内容，不显示原作者
      * ``quote``           —— 引用包装，自动加 "📨 来自 X" 前缀
      * ``link_only``       —— 公开超级群可点链接 ``t.me/c/<bare>/<msg_id>``
  - 风控集成：每次转发先 ``engine.acquire("forward_message", peer_id=...)``；不允许就丢弃
  - FloodWait 自动兜底：触发后 sleep(min(seconds,60)) 再重试一次，仍失败仅记 error
  - 全部异常吞掉走 ``ctx.log("error", ...)``，单条失败不影响后续 incoming 消息派发

duplicate 模式说明（类似 AutoRepeat 复读逻辑）：
  - 统计同一 chat 内 **不同用户** 发送相同文本的数量（同一用户多次发送只算 1 次）
  - 达到阈值时触发转发，每日去重（同内容同群每天只触发一次，UTC+8 午夜重置）
  - 内容指纹：短文本用全文，长文本用前 50 字符 + 总长度

rule.config 形如：
    {
      "source_kind": "all" | "peers" | "keyword" | "duplicate",
      "source_peers": [-1001234567890, ...],
      "keyword": "紧急",
      "duplicate_window": 60,
      "duplicate_threshold": 3,
      "target_chat_id": -1001112223334,
      "mode": "forward_native" | "copy_text" | "quote" | "link_only",
      "include_media": true,
      "header": "[from team A]"
    }
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import defaultdict
from typing import Any

from telethon import events

# 模块化重构后统一用绝对 import，方便第三方插件解压到 data/plugins/installed/
# 时也能复用同一套写法。
from app.db.models.feature import FEATURE_FORWARD
from app.worker.plugins.base import Plugin, PluginContext, public_entity_display_name, register


@register
class ForwardPlugin(Plugin):
    """消息转发插件实现。"""

    key = FEATURE_FORWARD
    display_name = "消息转发"
    owner_only = False

    async def on_message(
        self, ctx: PluginContext, event: events.NewMessage.Event
    ) -> None:
        """对每条 incoming 消息遍历所有 enabled 规则，逐条尝试转发。

        与 auto_reply 不同：转发是"一对多"语义——一条消息可能命中多条规则
        （比如同时配了"全转到收藏夹"和"含关键词转到团队群"），所以这里 **不 break**，
        每条命中规则都各自走一遍流水线。
        """
        if not ctx.rules:
            return

        for rule in ctx.rules:
            cfg: dict[str, Any] = rule.config or {}
            # 1) 源筛选
            if not _match_source(event, cfg):
                continue
            # 2) 媒体过滤：默认 include_media=True（兼容旧配置），仅显式 False 才跳过
            include_media = cfg.get("include_media", True)
            if not include_media and event.message and event.message.media:
                continue
            # 3) target_chat_id：缺失 / 非法时跳过，避免误转回当前 chat
            target_raw = cfg.get("target_chat_id")
            try:
                target = int(target_raw)
            except (TypeError, ValueError):
                if ctx.log is not None:
                    await ctx.log("warn", "[forward] 缺少 target_chat_id，跳过", rule_id=rule.id)
                continue

            # 4) 真正发送 + FloodWait 自动重试一次
            try:
                await self._do_forward(ctx, event, cfg, target)
            except Exception as exc:  # noqa: BLE001
                # FloodWait 单独处理：写 override + sleep + retry 一次
                if _is_flood_wait(exc):
                    seconds = int(getattr(exc, "seconds", 0) or 0)
                    if ctx.log is not None:
                        await ctx.log(
                            "warning",
                            f"[forward] floodwait {seconds}s, sleep & retry once",
                            rule_id=rule.id,
                        )
                    # 把异常回灌给 engine（写 override + 标 floodwait 状态）
                    try:
                        await ctx.engine.on_flood_wait("forward_message", exc)
                    except Exception:  # noqa: BLE001
                        # engine 失败不影响 retry 流程
                        pass
                    await asyncio.sleep(min(seconds, 60))
                    try:
                        await self._do_forward(ctx, event, cfg, target)
                    except Exception as exc2:  # noqa: BLE001
                        if ctx.log is not None:
                            await ctx.log(
                                "error",
                                f"[forward] retry failed: {type(exc2).__name__}: {exc2}",
                                rule_id=rule.id,
                            )
                else:
                    # 其它异常仅写日志，保证不影响后续规则
                    if ctx.log is not None:
                        await ctx.log(
                            "error",
                            f"[forward] failed: {type(exc).__name__}: {exc}",
                            rule_id=rule.id,
                        )

    async def _do_forward(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        cfg: dict[str, Any],
        target: int,
    ) -> None:
        """实际执行一次转发：风控 acquire → 按 mode 走不同 send 路径。"""
        # ── 风控 acquire ──
        decision = await ctx.engine.acquire(
            ctx.account_id, "forward_message", peer_id=target
        )
        if not decision.allowed:
            if ctx.log is not None:
                await ctx.log(
                    "info",
                    f"[forward] 被风控丢弃 outcome={decision.outcome}",
                )
            return
        if decision.wait_seconds and decision.wait_seconds > 0:
            await asyncio.sleep(float(decision.wait_seconds))

        mode = cfg.get("mode", "forward_native")
        header = cfg.get("header") or ""
        client = ctx.client

        if mode == "forward_native":
            # 原生转发：携带原作者署名（公开消息可点跳源）
            await event.message.forward_to(target)
        elif mode == "copy_text":
            # 复制文本：不带原作者，header + 原文（空文本 fallback "(empty)"）
            text = (header + (event.message.text or "")) or "(empty)"
            await client.send_message(target, text)
        elif mode == "quote":
            # 引用包装：📨 来自 <群名/用户名/chat_id>
            try:
                src = await event.get_chat()
            except Exception:  # noqa: BLE001
                src = None
            chat_label = public_entity_display_name(src, fallback_id=event.chat_id)
            body_text = event.message.text or "(no text)"
            body = f"{header}📨 来自 {chat_label}\n\n{body_text}"
            await client.send_message(target, body)
        elif mode == "link_only":
            # 仅链接：公开超级群 / 频道生成 https://t.me/c/<bare>/<msg_id>；
            # 非公开会话退化成 "消息引用：chat=... id=..."
            link = _build_msg_link(event)
            await client.send_message(target, header + link if header else link)
        else:
            # 兜底：未知 mode 不发送，写一条 warn 方便排查
            if ctx.log is not None:
                await ctx.log("warn", f"[forward] 未知 mode={mode!r}，跳过")


# ─────────────────────────────────────────────────────
# 复读检测缓存（类似 AutoRepeat 逻辑）
# ─────────────────────────────────────────────────────

# 结构：{chat_id: {text_hash: {user_id: timestamp}}}
# 记录每个 chat 内不同用户发送相同文本的时间，用于统计"不同用户数"
_dup_cache: dict[int, dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
_dup_cache_last_cleanup: float = 0.0
_DUP_CACHE_CLEANUP_INTERVAL = 300  # 每 5 分钟清理一次

# 每日去重：{chat_id: {content_key}}，同一内容同群每天只触发一次
_daily_fired: dict[int, set[str]] = defaultdict(set)
_daily_fired_day_key: int = 0  # UTC+8 天数标记，用于检测跨天重置


def _content_key(text: str) -> str:
    """生成内容指纹：短文本用全文，长文本用前 50 字符 + 总长度。"""
    stripped = text.strip()
    if len(stripped) <= 50:
        return stripped
    return stripped[:50] + str(len(stripped))


def _utc8_day_key() -> int:
    """返回 UTC+8 时区下的天数标记，用于每日去重重置。"""
    now_utc8 = time.time() + 8 * 3600
    return int(now_utc8 // 86400)


def _maybe_reset_daily() -> None:
    """检查是否跨天（UTC+8 午夜），是则清空每日去重缓存。"""
    global _daily_fired_day_key
    today = _utc8_day_key()
    if today != _daily_fired_day_key:
        _daily_fired.clear()
        _daily_fired_day_key = today


def _record_message(chat_id: int, user_id: int, text: str) -> str:
    """记录消息文本指纹和发送者，返回 hash 值。

    同一用户对同一文本只保留最新时间戳（复读场景下同一人发多次只算 1 个用户）。
    """
    global _dup_cache_last_cleanup
    text_hash = hashlib.md5(text.strip().encode()).hexdigest() if text.strip() else ""
    if not text_hash:
        return text_hash
    now = time.monotonic()
    _dup_cache[chat_id][text_hash][user_id] = now
    # 定期清理
    if now - _dup_cache_last_cleanup > _DUP_CACHE_CLEANUP_INTERVAL:
        _cleanup_dup_cache(now)
        _dup_cache_last_cleanup = now
    return text_hash


def _check_duplicate(chat_id: int, text: str, window: int, threshold: int) -> bool:
    """检查指定 chat 内 **不同用户** 发送相同文本的数量是否 >= threshold。

    逻辑：
    1. 统计 window 秒内有多少个不同 user_id 发送过相同文本
    2. 达到 threshold 且未被每日去重拦截时返回 True
    3. 触发后记入每日去重，同内容同群当天不再重复触发
    """
    text_hash = hashlib.md5(text.strip().encode()).hexdigest() if text.strip() else ""
    if not text_hash:
        return False

    now = time.monotonic()
    cutoff = now - window
    user_map = _dup_cache.get(chat_id, {}).get(text_hash, {})

    # 统计 window 内的不同用户数
    distinct_users = sum(1 for ts in user_map.values() if ts >= cutoff)
    if distinct_users < threshold:
        return False

    # 每日去重检查
    _maybe_reset_daily()
    ck = _content_key(text)
    if ck in _daily_fired.get(chat_id, set()):
        return False

    # 标记为今日已触发
    _daily_fired[chat_id].add(ck)
    return True


def _cleanup_dup_cache(now: float | None = None) -> None:
    """清理过期的复读缓存条目。"""
    if now is None:
        now = time.monotonic()
    cutoff = now - 600  # 保留最近 10 分钟的数据（冗余，确保 window=300 也安全）
    expired_chats: list[int] = []
    for chat_id, hashes in _dup_cache.items():
        expired_hashes: list[str] = []
        for text_hash, user_map in hashes.items():
            # 清理过期用户记录
            expired_users = [uid for uid, ts in user_map.items() if ts < cutoff]
            for uid in expired_users:
                del user_map[uid]
            if not user_map:
                expired_hashes.append(text_hash)
        for h in expired_hashes:
            del hashes[h]
        if not hashes:
            expired_chats.append(chat_id)
    for cid in expired_chats:
        del _dup_cache[cid]


# ─────────────────────────────────────────────────────
# 工具：源筛选 / chat_id 等价展开 / 链接生成 / FloodWait 判定
# ─────────────────────────────────────────────────────
def _match_source(event: Any, cfg: dict[str, Any]) -> bool:
    """按 ``source_kind`` 决定当前消息是否进入转发流水线。

    - ``all``       —— 永远命中（仅靠 include_media / target 兜底过滤）
    - ``peers``     —— 与 ``source_peers`` 列表做"等价 chat_id"交集
    - ``keyword``   —— 文本（小写化）包含关键词；空关键词视为不命中（避免误炸）
    - ``duplicate`` —— 重复消息检测：同一 chat 内相同文本在时间窗口内出现次数达到阈值
    """
    kind = cfg.get("source_kind", "all")
    if kind == "all":
        return True

    if kind == "peers":
        peers = _coerce_int_list(cfg.get("source_peers") or [])
        if not peers:
            return False
        target_set = _expand_chat_id(int(event.chat_id)) if event.chat_id is not None else set()
        for p in peers:
            if target_set & _expand_chat_id(int(p)):
                return True
        return False

    if kind == "keyword":
        kw = (cfg.get("keyword") or "").strip().lower()
        if not kw:
            return False
        text = ""
        try:
            text = event.message.text or event.raw_text or ""
        except Exception:  # noqa: BLE001
            text = getattr(event, "raw_text", "") or ""
        return kw in text.lower()

    if kind == "duplicate":
        # 获取消息文本
        text = ""
        try:
            text = event.message.text or event.raw_text or ""
        except Exception:  # noqa: BLE001
            text = getattr(event, "raw_text", "") or ""
        if not text.strip():
            return False
        window = int(cfg.get("duplicate_window") or 60)
        threshold = int(cfg.get("duplicate_threshold") or 3)
        chat_id = int(event.chat_id) if event.chat_id is not None else 0
        # 获取发送者 user_id；同一用户多次发送只算 1 人
        sender_id = getattr(event, "sender_id", None) or 0
        # 先记录再检查
        _record_message(chat_id, sender_id, text)
        return _check_duplicate(chat_id, text, window, threshold)

    return False


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


# Telegram 协议里 supergroup / channel 的 chat_id 都是 ``-100xxxxxxxxxx`` 形式；
# basic group 是 ``-xxxxxxxxxx``；私聊是正数。
# 用户从 t.me/c/<id>/<msg> 复制下来的是去掉 -100 的纯数字。
# 为了让用户填什么形式都能命中，把每个 id 展开成它所有合理的等价表示。
_CHANNEL_PREFIX = 1_000_000_000_000  # 即 1e12，supergroup/channel id 的固定前缀


def _expand_chat_id(raw: int) -> set[int]:
    """把一个 chat id 展开成所有可能的等价表示。

    例：
      - 1234567890       → 也能匹配 -1001234567890 / -1234567890
      - -1001234567890   → 同样展开到 1234567890 / -1234567890
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


def _build_msg_link(event: Any) -> str:
    """根据 chat_id 生成 t.me/c/<bare>/<msg_id> 链接；非超级群退化成可读字符串。"""
    cid = event.chat_id
    mid = getattr(event.message, "id", None) if getattr(event, "message", None) else None
    if cid is None or mid is None:
        return f"消息引用：chat={cid}, id={mid}"
    sid = str(cid)
    if sid.startswith("-100"):
        return f"https://t.me/c/{sid[4:]}/{mid}"
    return f"消息引用：chat={cid}, id={mid}"


def _is_flood_wait(exc: Exception) -> bool:
    """判断异常是否为 ``FloodWaitError``（不强依赖 telethon 的具体类路径）。"""
    try:
        from telethon.errors import FloodWaitError

        return isinstance(exc, FloodWaitError)
    except Exception:  # pragma: no cover - 测试环境无 telethon 时兜底
        return type(exc).__name__ == "FloodWaitError"


# ─────────────────────────────────────────────────────
# 暴露给 dry-run / 测试使用的内部工具
# ─────────────────────────────────────────────────────
def _dry_run_match(
    cfg: dict[str, Any],
    text: str,
    chat_id: int | None = None,
) -> tuple[bool, str | None]:
    """供 API ``dry-run`` 调用：纯函数判断"是否命中"+ 返回一句话描述。

    返回的 ``output`` 是给前端展示的 "would forward to <target>" 文案，
    与真正转发并无关系（不会真的下发任何 send_message）。

    注意：duplicate 模式在 dry-run 时只能模拟一次，无法真正检测重复。
    返回提示信息说明需要实际运行才能检测。
    """

    class _FakeMsg:
        media = None

        def __init__(self, t: str) -> None:
            self.text = t
            self.id = 0

    class _FakeEvent:
        def __init__(self, t: str, cid: int | None) -> None:
            self.raw_text = t
            self.chat_id = cid if cid is not None else 0
            self.message = _FakeMsg(t)
            self.is_private = False
            self.is_group = False
            self.is_channel = False

    event = _FakeEvent(text, chat_id)
    kind = cfg.get("source_kind", "all")

    # duplicate 模式 dry-run 特殊处理
    if kind == "duplicate":
        target = cfg.get("target_chat_id")
        mode = cfg.get("mode", "forward_native")
        window = int(cfg.get("duplicate_window") or 60)
        threshold = int(cfg.get("duplicate_threshold") or 3)
        return (
            True,
            f"[dry-run] 复读检测模式：实际运行时，当 {window}s 内 ≥{threshold} 个不同用户发送相同文本时触发转发，转发到 {target} (mode={mode})。同内容同群每天只触发一次。",
        )

    if not _match_source(event, cfg):
        return False, None
    target = cfg.get("target_chat_id")
    mode = cfg.get("mode", "forward_native")
    return True, f"would forward to {target} (mode={mode})"


PLUGIN_CLASS = ForwardPlugin

__all__ = [
    "ForwardPlugin",
    "PLUGIN_CLASS",
    "_build_msg_link",
    "_dry_run_match",
    "_expand_chat_id",
    "_match_source",
]
