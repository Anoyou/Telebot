"""10 以内算数题插件 Manifest。"""

from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="math10",
    display_name="随机算数题",
    version="1.0.0",
    author="TelePilot Official",
    description="由交互 Bot 开启一局 10 以内算数题，群内第一个答对者获得奖金",
    category="interactive",
    interaction_profile="session_game",
    interaction_entries=[
        {
            "key": "start_math_game",
            "title": "随机算数题",
            "description": "转账命中或模块关键词触发后，由交互 Bot 开启一局随机算数题。",
            "interaction_profile": "session_game",
            "launch_mode": "bridge",
            "session_scope": "chat",
            "events": ["payment_confirmed", "keyword", "message", "callback_query", "session_close"],
            "preserve_command_trigger": True,
            "payload_contract": {
                "required_envelope": ["source", "actor", "trigger", "session"],
                "required_event_fields": ["type", "chat_id"],
            },
            "result_contract": {
                "actions": ["send_message", "send_photo", "send_file", "end_session", "result", "settlement"],
                "send_via": ["interaction_bot", "userbot_reply", "bbot_notice"],
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
                    "valid_seconds": {
                        "type": "integer",
                        "title": "平台会话有效期（秒）",
                        "default": 900,
                        "minimum": 30,
                        "maximum": 86400,
                    },
                },
                "required": ["prize"],
            },
        }
    ],
    permissions=["send_message"],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "x-usage-guide": "随机算数题主要由交互规则启动：在交互中心选择“随机算数题”入口，配置群内关键词或付款触发后，普通 Bot 负责高频答题交互，奖金和结算说明仍由 userbot/平台通道承接。",
        "additionalProperties": False,
        "properties": {},
    },
)

__all__ = ["MANIFEST"]
