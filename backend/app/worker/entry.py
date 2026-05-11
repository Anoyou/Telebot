"""Worker 子进程 spawn entrypoint。

为什么需要这一层薄包装？

multiprocessing 在 spawn 模式下会在子进程里**重新 import** 目标函数所在的模块；
``app.worker.runtime`` 顶层就 ``from ..db.base import AsyncSessionLocal``，
而 ``app.db.base`` 在 import 时就会 ``create_async_engine``，按主进程默认池
预留 5+2 条连接。多账号 worker 场景下这个浪费会被线性放大。

把 ``TELEBOT_WORKER_PROC=1`` 在 import ``runtime`` **之前**写到环境变量里，
``app.db.base._engine_kwargs`` / ``app.redis_client._max_connections`` 即可
按更紧的 ``*_worker`` 默认值开池。这就是本模块存在的唯一目的。
"""

from __future__ import annotations

import os


def worker_entry(account_id: int) -> None:
    """子进程真正的 entrypoint；在导入重型模块前先标记进程角色。"""
    os.environ["TELEBOT_WORKER_PROC"] = "1"
    # 触发重型 import 的位置——在 env 之后才执行
    from .runtime import worker_main

    worker_main(account_id)
