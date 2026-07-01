"""核心 builtin 兼容包索引。

不要在包入口导入所有插件实现；worker 会按账号启用项懒加载。
游戏和图片类官方可选插件已经物理迁出 Core，由远程官方插件仓库分发。
"""

__all__ = [
    "auto_reply",
    "autorepeat",
    "forward",
    "scheduler",
]
