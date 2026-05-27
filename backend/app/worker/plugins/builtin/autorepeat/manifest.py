"""autorepeat 插件 manifest。

规则驱动：每条 rule 对应一个群组的复读配置。
rule.config 字段：
  - target_chat_id: int   监控的群组 ID（必填）
  - time_window: int      时间窗口秒数（默认 300）
  - min_users: int        触发复读所需不同用户数（默认 5）
"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="autorepeat",
    display_name="自动复读",
    version="1.0.0",
    author="TeleBoxOrg",
    description="当群组中多名用户在指定时间内发送相同内容时自动复读",
    category="automation",
    permissions=["send_message", "edit_message", "read_chat", "resolve_entity"],
    config_schema={
        "type": "object",
        "x-ui-mode": "rules",
        "additionalProperties": False,
        "required": ["target_chat_id"],
        "properties": {
            "target_chat_id": {
                "type": "integer",
                "title": "群组 ID",
                "description": "监控的群组 chat_id（Telethon marked ID 格式）",
            },
            "time_window": {
                "type": "integer",
                "title": "时间窗口（秒）",
                "default": 300,
                "description": "统计相同消息的时间窗口，默认 300（5分钟）",
            },
            "min_users": {
                "type": "integer",
                "title": "最少触发人数",
                "default": 5,
                "description": "触发复读所需的不同用户数，默认 5",
            },
        },
    },
)
