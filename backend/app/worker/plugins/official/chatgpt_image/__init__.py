"""ChatGPT2API 官方可选插件。"""

from .manifest import MANIFEST
from .plugin import ChatGPTImagePlugin

PLUGIN_CLASS = ChatGPTImagePlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
