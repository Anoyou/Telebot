"""api 子包：把已实现的 router 模块导出，便于 main.py 一处 import。"""

from . import accounts, auth, features, logs, plugins, rate_limit, rules  # noqa: F401

__all__ = [
    "accounts",
    "auth",
    "features",
    "logs",
    "plugins",
    "rate_limit",
    "rules",
]
