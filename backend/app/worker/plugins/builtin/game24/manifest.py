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
)

__all__ = ["MANIFEST"]
