"""插件子包：提供 PluginContext / Plugin 基类、注册表、loader 与内置插件集合。"""

from .base import Plugin, PluginContext, all_plugins, get_plugin, register

__all__ = ["Plugin", "PluginContext", "all_plugins", "get_plugin", "register"]
