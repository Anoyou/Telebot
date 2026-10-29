"""signature_mw 中间件示例 manifest（``middleware`` 只写在 plugin.json）。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "signature": {"type": "string", "default": "— 由 TelePilot 发送", "title": "署名"},
        "compact_tables": {"type": "boolean", "default": False, "title": "所有插件的表格改为紧凑样式"},
    },
}

MANIFEST = Manifest(
    key="signature_mw",
    display_name="统一署名（中间件示例）",
    version="0.1.0",
    min_telepilot_version="0.109.0-beta.1",
    author="examples",
    description="演示消息管线中间件：给交互 Bot 发出的文本消息统一加署名，按需把表格改成紧凑样式，并在 delivered 阶段统计投递结果。",
    usage=(
        "启用后，本账号所有插件经交互 Bot 发出的文本消息末尾加上配置的署名（Rich 消息不加）；"
        "开启「表格紧凑」后所有插件的表格改为紧凑样式（render_profile）；"
        "投递成功 / 失败次数按动作类型记在插件存储里，可在插件详情的运行统计看到中间件耗时。"
    ),
    category="utility",
    permissions=["message_middleware"],
    requires_platform_capabilities=["interaction_bot"],
    config_schema=CONFIG_SCHEMA,
    capabilities={},
)

__all__ = ["CONFIG_SCHEMA", "MANIFEST"]
