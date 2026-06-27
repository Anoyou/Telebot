"""随机算数题插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from __future__ import annotations

from .manifest import MANIFEST
from .plugin import Math10Plugin

PLUGIN_CLASS = Math10Plugin

__all__ = ["Math10Plugin", "MANIFEST", "PLUGIN_CLASS"]
