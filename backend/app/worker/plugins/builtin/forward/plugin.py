"""内置插件：消息转发（PRD §B）。

支持能力：
  - 三种源筛选：``all`` / ``peers``（chat_id 列表）/ ``keyword``（文本包含）
  - ``include_media`` 开关：False 时跳过含媒体的消息（仅文本）
  - 四种转发方式（``mode``）：
      * ``forward_native``  —— 原生转发，保留原作者署名（``message.forward_to``）
      * ``copy_text``       —— 仅复制文字内容，不显示原作者
      * ``quote``           —— 引用包装，自动加 "📨 来自 X" 前缀
      * ``link_only``       —— 公开超级群可点链接 ``t.me/c/<bare>/<msg_id>``
  - 风控集成：每次转发先 ``engine.acquire("forward_message", peer_id=...)``；不允许就丢弃
  - FloodWait 自动兜底：触发后 sleep(min(seconds,60)) 再重试一次，仍失败仅记 error
  - 全部异常吞掉走 ``ctx.log("error", ...)``，单条失败不影响后续 incoming 消息派发

rule.config 形如：
    {
      "source_kind": "all" | "peers" | "keyword",
      "source_peers": [-1001234567890, ...],
      "keyword": "紧急",
      "target_chat_id": -1001112223334,
      "mode": "forward_native" | "copy_text" | "quote" | "link_only",
      "include_media": true,
      "header": "[from team A]"
    }
"""

from __future__ import annotations

import asyncio
from typing import Any

from telethon import events

# 模块化重构后统一用绝对 import，方便第三方插件解压到 data/plugins/installed/
# 时也能复用同一套写法。
from app.db.models.feature import FEATURE_FORWARD
from app.worker.plugins.base import Plugin, PluginContext, register


@register
class ForwardPlugin(Plugin):
    """消息转发插件实现。"""

    key = FEATURE_FORWARD
    display_name = "消息转发"

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
            # 3) target_chat_id 兜底校验：缺失 / 非法时直接跳过并记日志
            target_raw = cfg.get("target_chat_id")
            try:
                target = int(target_raw)
            except (TypeError, ValueError):
                if ctx.log is not None:
                    await ctx.log(
                        "warn",
                        f"[forward] 规则 #{rule.id} 缺少合法 target_chat_id：{target_raw!r}",
                        rule_id=rule.id,
                    )
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
            chat_label = (
                getattr(src, "title", None)
                or getattr(src, "username", None)
                or getattr(src, "first_name", None)
                or str(event.chat_id)
            )
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
# 工具：源筛选 / chat_id 等价展开 / 链接生成 / FloodWait 判定
# ─────────────────────────────────────────────────────
def _match_source(event: Any, cfg: dict[str, Any]) -> bool:
    """按 ``source_kind`` 决定当前消息是否进入转发流水线。

    - ``all``     —— 永远命中（仅靠 include_media / target 兜底过滤）
    - ``peers``   —— 与 ``source_peers`` 列表做"等价 chat_id"交集
    - ``keyword`` —— 文本（小写化）包含关键词；空关键词视为不命中（避免误炸）
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
