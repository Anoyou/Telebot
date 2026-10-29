"""敏感词守卫：inbound 丢弃、outbound 打码 / 拒绝（消息管线中间件示例，Plugin API 1.2）。

中间件签名统一为 ``async def handler(self, ctx, value, mw)``：

- ``value`` 是只读视图（顶层是普通 dict，嵌套值只读）；要改嵌套值就整体替换；
- outbound 返回改过的动作 dict（只能改正文 / Rich / 按钮等白名单字段），返回 None 表示原样；
- ``mw.reject(code, message)`` 拒绝发送这条动作，``mw.drop(reason)`` 丢弃入站事件。
"""

from __future__ import annotations

from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

MASK = "＊＊"


def _words(ctx: PluginContext) -> list[str]:
    raw = (ctx.config or {}).get("words")
    if not isinstance(raw, list):
        raw = ["违禁词"]
    return [str(item) for item in raw if str(item or "").strip()]


def _hit(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _mask(text: str, words: list[str]) -> str:
    for word in words:
        text = text.replace(word, MASK)
    return text


def _event_text(event: Any) -> str:
    message = event.get("message") if hasattr(event, "get") else None
    if message is not None and message.get("text"):
        return str(message.get("text") or "")
    return str(event.get("message_text") or event.get("text") or "")


@register
class WordGuardMiddleware(Plugin):
    key = "word_guard_mw"
    display_name = "敏感词守卫（中间件示例）"

    async def on_inbound(self, ctx: PluginContext, event: Any, mw: Any) -> None:
        if (ctx.config or {}).get("drop_inbound", True) is False:
            return None
        if _hit(_event_text(event), _words(ctx)):
            mw.drop("命中敏感词")
        return None

    async def on_outbound(self, ctx: PluginContext, action: dict[str, Any], mw: Any) -> dict[str, Any] | None:
        words = _words(ctx)
        changed = False
        text = action.get("text")
        rich = action.get("rich_message")
        texts = [text] if isinstance(text, str) else []
        for key in ("markdown", "html"):
            if rich is not None and isinstance(rich.get(key), str):
                texts.append(rich.get(key))
        if not any(_hit(item, words) for item in texts):
            return None
        if (ctx.config or {}).get("outbound_mode") == "reject" and mw.action_type != "stream_preview":
            mw.reject("sensitive_word", "消息含敏感词，已拦截")
        if isinstance(text, str) and _hit(text, words):
            action["text"] = _mask(text, words)
            changed = True
        if rich is not None:
            new_rich = mw.thaw(rich)  # 只读视图 → 可修改副本，改完整体替换
            for key in ("markdown", "html"):
                if isinstance(new_rich.get(key), str) and _hit(new_rich[key], words):
                    new_rich[key] = _mask(new_rich[key], words)
                    changed = True
            action["rich_message"] = new_rich
        return action if changed else None
