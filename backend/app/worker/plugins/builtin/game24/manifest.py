"""24 点游戏插件 Manifest。

Config Schema 说明：
- level: "global" 的字段为全局配置，所有账号共享
- 无 level 或 level: "account" 的字段为账号级配置
- 配置合并顺序：schema defaults < global config < account config
"""
from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="game24",
    display_name="24点游戏",
    version="1.1.0",
    author="builtin",
    description="随机生成 24 点题目，群内竞速答题，第一名获得奖金",
    category="interactive",
    interaction_entries=[
        {
            "key": "start_paid_game",
            "title": "付费开局",
            "description": "转账命中或模块关键词触发后，由交互 Bot 开启一局 24 点。",
            "session_scope": "chat",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "prize": {
                        "type": "integer",
                        "title": "奖金",
                        "default": 123,
                        "minimum": 1,
                    },
                    "timeout": {
                        "type": "integer",
                        "title": "答题限时（秒）",
                        "default": 500,
                        "minimum": 30,
                        "maximum": 3600,
                    },
                },
                "required": ["prize"],
            },
        }
    ],
    permissions=["send_message", "edit_message", "read_chat", "delete_message"],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "title": "触发指令名",
                "description": "不含系统命令前缀，可使用中文；不要包含空格。例：24d、开24点",
                "default": "24d",
                "minLength": 1,
                "maxLength": 32,
                "pattern": "^\\S+$",
                "level": "account",
            },
            "timeout": {
                "type": "integer",
                "title": "答题限时（秒）",
                "description": "超过此时间无人答对，游戏自动结束。",
                "default": 500,
                "minimum": 30,
                "maximum": 3600,
                "level": "account",
            },
        },
        "required": ["command", "timeout"],
    },
)

__all__ = ["MANIFEST"]
