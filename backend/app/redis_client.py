"""Redis 异步客户端封装。"""

from __future__ import annotations

import redis.asyncio as redis_async

from .settings import settings

# 全局共享实例（按 use case 复用连接池）
_pool: redis_async.ConnectionPool | None = None


def get_pool() -> redis_async.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis_async.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=64,
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
