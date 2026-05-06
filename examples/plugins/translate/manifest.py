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
)

__all__ = ["MANIFEST"]
