"""内置插件包索引。

不要在包入口导入所有插件实现；worker 会按账号启用项懒加载。
"""

__all__ = ["auto_reply", "autorepeat", "forward", "game24", "scheduler"]
