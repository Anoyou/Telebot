"""word_guard_mw 中间件示例 manifest。

``middleware`` 只写在 plugin.json（唯一可信来源；旧宿主的 Manifest 不认识这个参数）。
"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {"type": "array", "items": {"type": "string"}, "default": ["违禁词"], "title": "敏感词"},
        "outbound_mode": {"type": "string", "enum": ["mask", "reject"], "default": "mask", "title": "出站处理方式"},
        "drop_inbound": {"type": "boolean", "default": True, "title": "丢弃含敏感词的入站消息"},
    },
}

MANIFEST = Manifest(
    key="word_guard_mw",
    display_name="敏感词守卫（中间件示例）",
    version="0.1.0",
    min_telepilot_version="0.109.0-beta.1",
    author="examples",
    description="演示消息管线中间件：入站丢弃含敏感词的消息，出站把敏感词打码或拒绝发送，流式中间态同样打码。",
    usage=(
        "启用后对本账号所有插件生效：群里收到含敏感词的消息时，Event Bus 插件不会收到它（inbound 丢弃）；"
        "任何插件要发出的消息里出现敏感词时按配置打码（默认）或整条拒绝（outbound）；"
        "AI 流式回复的中间态也会打码（stream_preview）。敏感词在插件配置里维护。"
    ),
    category="utility",
    permissions=["message_middleware"],
    requires_platform_capabilities=[],
    config_schema=CONFIG_SCHEMA,
    capabilities={},
)

__all__ = ["CONFIG_SCHEMA", "MANIFEST"]
