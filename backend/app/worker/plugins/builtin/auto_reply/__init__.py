"""auto_reply 插件包入口：

- 暴露 ``PLUGIN_CLASS`` / ``MANIFEST`` 给 loader 扫描使用
- re-export plugin.py 顶层公开符号，保证旧的 import 路径
  (``from app.worker.plugins.builtin.auto_reply import AutoReplyPlugin / _dry_run_match`` 等)
  在目录化重构后继续可用
"""

from .manifest import MANIFEST
from .plugin import (
    AutoReplyPlugin,
    _dry_run_match,
    _match,
    _render,
    _scope_ok,
)

# loader.discover_plugins 读取这两个常量，无须显式 @register
PLUGIN_CLASS = AutoReplyPlugin

__all__ = [
    "AutoReplyPlugin",
    "MANIFEST",
    "PLUGIN_CLASS",
    "_dry_run_match",
    "_match",
    "_render",
    "_scope_ok",
]
