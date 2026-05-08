"""translate 示例插件 manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="translate",
    display_name="翻译助手",
    version="0.1.0",
    author="examples",
    description="命令 ,fy <lang|auto>：翻译被回复消息",
    permissions=["read_chat", "edit_message"],
    config_schema={
        "type": "object",
        "properties": {
            "default_lang": {
                "type": "string", "title": "默认目标语言", "default": "auto",
                "description": "auto=自动检测，zh/en/ja 等",
            },
            "llm_provider": {
                "type": "string", "title": "LLM 提供商",
                "description": "使用哪个 LLM 做翻译（留空用默认）",
            },
        },
    },
)

__all__ = ["MANIFEST"]
