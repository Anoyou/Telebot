"""插件页面示例：worker 里计数 ping，卡片 / 页面 / 菜单在主进程里只读展示。

- ``on_event``（worker）：已允许会话出现 ``ping`` 时 ``ctx.storage.incr("pings")``；
- ``on_ui_card``：读 ``ctx.storage``（0.111.0+ 卡片钩子里是只读视图，旧宿主为 None）；
- ``on_ui_action``：菜单项 ``summarize`` 与页面 Bridge ``action.invoke`` 共用，``context.page_id``
  区分来源；返回 ``data`` 会原样交给页面。
"""

from __future__ import annotations

from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register
from app.worker.plugins.events import event_from_interaction_payload


async def _read_pings(ctx: PluginContext) -> int:
    storage = getattr(ctx, "storage", None)
    if storage is None:  # 0.111.0 之前的宿主：卡片钩子里没有 ctx.storage
        return 0
    return int(await storage.get("pings", 0) or 0)


@register
class UIPageDemoPlugin(Plugin):
    key = "ui_page_demo"
    display_name = "插件页面示例"

    async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]]:
        event = event_from_interaction_payload(payload)
        if event.type != "message" or event.message.text.strip().lower() != "ping":
            return []
        if ctx.storage is not None:
            await ctx.storage.incr("pings")
        return []

    async def on_ui_card(self, ctx: PluginContext, card_id: str, context: dict[str, Any]) -> dict[str, Any] | None:
        if card_id == "ping_stats":
            return {"pings": await _read_pings(ctx)}
        return None

    async def on_ui_action(self, ctx: PluginContext, action_id: str, context: dict[str, Any]) -> dict[str, Any] | None:
        if action_id != "summarize":
            return {"ok": False, "message": f"未知操作：{action_id}"}
        pings = await _read_pings(ctx)
        source = "页面" if context.get("page_id") else "菜单"
        return {"message": f"累计 {pings} 次 ping（来自{source}）", "refresh": False, "data": {"pings": pings}}
