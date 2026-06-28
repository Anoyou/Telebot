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
    author="TelePilot Official",
    description="随机生成 24 点题目，群内竞速答题，第一名获得奖金",
    usage="24 点游戏支持两种调度：管理员可发送命令直接由 userbot 启动；群友也可通过交互中心配置的关键词或付款触发，由交互 Bot 承接答题，转账和发奖仍由 userbot/平台结算通道处理。",
    category="interactive",
    event_subscriptions=[
        {
            "source": ["userbot"],
            "events": ["command"],
            "scope": "owner_only",
            "entry_key": "admin_command",
        },
        {
            "source": ["interaction_bot"],
            "events": ["message", "callback_query"],
            "scope": "rule_bound",
            "entry_key": "start_paid_game",
        },
        {
            "source": ["external_payment_notice", "userbot"],
            "events": ["payment_confirmed"],
            "scope": "rule_bound",
            "entry_key": "start_paid_game",
        },
    ],
    capabilities={},
    interaction_profile="session_game",
    interaction_entries=[
        {
            "key": "start_paid_game",
            "title": "付费开局",
            "description": "转账命中或模块关键词触发后，由交互 Bot 开启一局 24 点。",
            "interaction_profile": "session_game",
            "launch_mode": "hybrid",
            "events": ["payment_confirmed", "keyword", "message", "callback_query", "session_close"],
            "session_scope": "chat",
            "preserve_command_trigger": True,
            "command_fallback": {
                "enabled": True,
                "command": "24d",
                "mode": "hint_only",
            },
            "payload_contract": {
                "required_envelope": ["source", "actor", "trigger", "session"],
                "required_event_fields": ["type", "chat_id"],
            },
            "result_contract": {
                "actions": ["send_message", "send_photo", "send_file", "end_session", "result", "settlement"],
                "send_via": ["interaction_bot", "userbot_reply"],
            },
            "settlement": {
                "mode": "announce_only",
                "winner_field": "actor.user_id",
                "amount_field": "prize",
            },
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
                    "valid_seconds": {
                        "type": "integer",
                        "title": "平台会话有效期（秒）",
                        "minimum": 30,
                        "maximum": 86400,
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
        "x-usage-guide": "24 点游戏支持两种调度：管理员可发送 {prefix}{command} 奖金金额 直接由 userbot 启动；群友也可通过交互中心配置的关键词或付款触发，由交互 Bot 承接答题，转账和发奖仍由 userbot/平台结算通道处理。",
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
