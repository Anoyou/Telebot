"""统一署名：outbound 加署名 / 声明 render_profile，delivered 统计投递结果（中间件示例，Plugin API 1.2）。"""

from __future__ import annotations

from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

DEFAULT_SIGNATURE = "— 由 TelePilot 发送"


@register
class SignatureMiddleware(Plugin):
    key = "signature_mw"
    display_name = "统一署名（中间件示例）"

    async def on_outbound(self, ctx: PluginContext, action: dict[str, Any], mw: Any) -> dict[str, Any] | None:
        config = ctx.config or {}
        changed = False
        signature = str(config.get("signature") or DEFAULT_SIGNATURE).strip()
        text = action.get("text")
        if signature and isinstance(text, str) and text and action.get("rich_message") is None and signature not in text:
            action["text"] = f"{text}\n\n{signature}"
            changed = True
        if config.get("compact_tables") is True and action.get("rich_message") is not None:
            # 不用自己改表格：声明 render_profile，平台 core.rich_compact 统一渲染（之后的插件中间件也能看到）
            profile = mw.thaw(action.get("render_profile") or {})
            profile.setdefault("table", {})["compact"] = True
            action["render_profile"] = profile
            changed = True
        return action if changed else None

    async def on_delivered(self, ctx: PluginContext, result: Any, mw: Any) -> None:
        # result 只有摘要（action_type / ok / error_code / chat_id / source_plugin_key …），没有正文
        if ctx.storage is None:
            return
        key = f"delivered:{result.get('action_type')}:{'ok' if result.get('ok') else 'fail'}"
        try:
            current = int(await ctx.storage.get(key) or 0)
        except (TypeError, ValueError):
            current = 0
        await ctx.storage.set(key, current + 1)
