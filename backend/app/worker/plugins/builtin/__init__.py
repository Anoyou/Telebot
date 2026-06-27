"""核心 builtin 兼容包索引。

不要在包入口导入所有插件实现；worker 会按账号启用项懒加载。
官方可选插件的源码位于 ``app.worker.plugins.official``，这里保留历史导入兼容名。
"""

__all__ = [
    "auto_reply",
    "autorepeat",
    "chatgpt_image",
    "codex_image",
    "forward",
    "game24",
    "math10",
    "scheduler",
]
