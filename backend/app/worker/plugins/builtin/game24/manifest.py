"""24 点游戏插件 Manifest。"""
from __future__ import annotations

from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="game24",
    display_name="24点游戏",
    version="1.0.0",
    author="builtin",
    description="随机生成 24 点题目，群内竞速答题，第一名获得奖金",
    permissions=["send_message", "edit_message", "read_chat", "delete_message"],
    config_schema={
        "type": "object",
        "properties": {
            "time_limit": {
                "type": "integer", "title": "答题时间（秒）", "default": 30,
                "minimum": 10, "maximum": 300,
            },
            "prize": {
                "type": "integer", "title": "奖金金额", "default": 100,
                "minimum": 0,
            },
            "max_players": {
                "type": "integer", "title": "最大参与人数", "default": 50,
                "minimum": 2, "maximum": 200,
            },
        },
    },
)

__all__ = ["MANIFEST"]
