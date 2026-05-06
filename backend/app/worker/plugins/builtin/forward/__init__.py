"""forward 插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from .manifest import MANIFEST
from .plugin import ForwardPlugin

PLUGIN_CLASS = ForwardPlugin

__all__ = ["ForwardPlugin", "MANIFEST", "PLUGIN_CLASS"]
