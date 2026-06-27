"""24 点游戏插件 —— 包导出。"""
from __future__ import annotations

from .manifest import MANIFEST
from .plugin import Game24Plugin

PLUGIN_CLASS = Game24Plugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
