"""codex_image 插件 manifest。

配置模式：单配置对象（模式 B），无规则列表。
account_feature.config 字段：
  - access_token: str   Codex Access Token（通常在 .codex/auth.json 中获取）
"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="codex_image",
    display_name="Codex 图片生成",
    version="1.0.0",
    author="TeleBoxOrg",
    description="通过 Codex API 调用 GPT 图片生成模型，支持纯文生图和参考图生成",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema={
        "type": "object",
        "properties": {
            "access_token": {
                "type": "string",
                "title": "Codex Access Token",
                "description": "从 .codex/auth.json 获取的 access token，用于鉴权 Codex API",
            },
            "model": {
                "type": "string",
                "title": "模型名称",
                "default": "gpt-5.4",
                "description": "Codex 使用的模型，默认 gpt-5.4",
            },
            "max_wait_seconds": {
                "type": "integer",
                "title": "最大等待时间（秒）",
                "default": 600,
                "description": "图片生成最大等待时间，默认 600（10分钟）",
            },
        },
    },
)
