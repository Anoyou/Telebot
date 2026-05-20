"""scheduler 插件 manifest。

Config Schema 说明：
- level: "global" 的字段为全局配置，所有账号共享
- 无 level 或 level: "account" 的字段为账号级配置
- 配置合并顺序：schema defaults < global config < account config
"""

from __future__ import annotations

from app.db.models.feature import FEATURE_SCHEDULER
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key=FEATURE_SCHEDULER,
    display_name="定时任务",
    version="0.2.0",
    author="builtin",
    description="cron / once / interval 定时触发动作（send_message / run_command / call_llm）",
    category="automation",
    permissions=["send_message", "send_file"],
    # level 字段说明：
    #   - "global": 全局配置，所有账号共享
    #   - "account": 账号级配置，按账号隔离（默认）
    config_schema={
        "type": "object",
        "x-ui-mode": "platform",
        "properties": {
            "default_notify": {
                "type": "boolean", "title": "执行后通知", "default": True,
                "level": "global",
            },
            "max_tasks": {
                "type": "integer", "title": "最大任务数", "default": 20,
                "minimum": 1, "maximum": 100,
                "level": "global",
            },
            "allowed_command_whitelist": {
                "type": "array",
                "title": "自动命令白名单",
                "items": {"type": "string"},
                "default": [],
                "description": "仅这些命令 key 允许由 scheduler/自动动作触发；示例：测试（不要写前缀）。",
                "level": "account",
            },
        },
    },
)

__all__ = ["MANIFEST"]
