"""Redis 异步客户端封装。"""

from __future__ import annotations

import os

import redis.asyncio as redis_async

from .settings import settings

# 全局共享实例（按 use case 复用连接池）
_pool: redis_async.ConnectionPool | None = None


def _max_connections() -> int:
    """主进程 vs worker 子进程使用不同上限。

    Worker 子进程的 IPC + RPUSH 通常只占用 1-2 条连接，没必要按主进程
    16 条预留；多账号场景能显著少分配 socket / fd。
    """
    if os.environ.get("TELEBOT_WORKER_PROC") == "1":
        return max(2, int(settings.redis_max_connections_worker or 4))
    return max(4, int(settings.redis_max_connections or 16))


def get_pool() -> redis_async.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis_async.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=_max_connections(),
        )
    return _pool


def get_redis() -> redis_async.Redis:
    """每次返回一个 Redis 客户端（共享 pool）。"""
    return redis_async.Redis(connection_pool=get_pool())


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
