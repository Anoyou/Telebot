"""scheduler 插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from .manifest import MANIFEST
from .plugin import SchedulerPlugin

PLUGIN_CLASS = SchedulerPlugin

__all__ = ["MANIFEST", "PLUGIN_CLASS", "SchedulerPlugin"]
