"""SQLAlchemy 异步引擎与基类。"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..settings import settings


class Base(DeclarativeBase):
    """所有模型继承此基类。"""


def _is_worker_process() -> bool:
    """``app.worker.entry.worker_entry`` 在 spawn 子进程内、import runtime 之前设置该环境变量；主进程不设。"""
    return os.environ.get("TELEBOT_WORKER_PROC") == "1"


def _engine_kwargs() -> dict:
    """按数据库类型 + 进程角色构造连接池参数；小 VPS 默认保持紧凑。

    Worker 子进程默认走更瘦的 ``*_worker`` 池：spawn 出来的子进程大多只用
    1-2 条连接，没必要每个都按主进程 5+2 预留，多账号场景能省几十 MB。
    """

    kwargs = {
        "echo": False,
        "pool_pre_ping": True,
    }
    if settings.database_url.startswith("postgresql"):
        if _is_worker_process():
            pool_size = max(1, int(settings.db_pool_size_worker or 1))
            max_overflow = max(0, int(settings.db_max_overflow_worker or 0))
        else:
            pool_size = max(1, int(settings.db_pool_size or 1))
            max_overflow = max(0, int(settings.db_max_overflow or 0))
        kwargs.update(
            {
                "pool_size": pool_size,
                "max_overflow": max_overflow,
                "pool_timeout": max(1, int(settings.db_pool_timeout or 30)),
            }
        )
    return kwargs


# 全局异步引擎与 session factory
engine = create_async_engine(settings.database_url, **_engine_kwargs())

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
