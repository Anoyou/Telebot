"""测试用文件型 SQLite 引擎：统一开 WAL。

SQLite 默认回滚日志模式下读事务会阻塞写者提交，几个短 session 交错时容易被误判成
``database is locked``。WAL 让读写互不阻塞（写者之间仍串行），更接近生产 PostgreSQL
的行为；但它**不能**修好“连接被取消打断后仍持有写锁”这类真实缺陷，那类问题要在
代码里修（见 System Agent run manager 的协作式关闭）。

注意：``sqlite+aiosqlite:///:memory:`` 默认所有 session 共用同一条连接
（StaticPool），并发 session 会互相提交对方的事务，掩盖并发问题；并发敏感的用例
请用本模块的文件型引擎，或 CI 里的 PostgreSQL（``TELEPILOT_TEST_POSTGRES_URL``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_file_sqlite_engine(
    path: str | Path,
    *,
    busy_timeout_seconds: float | None = None,
    **kwargs: Any,
) -> AsyncEngine:
    """创建开启 WAL 的文件型 aiosqlite 引擎；teardown 时调用方负责 ``dispose()``。"""

    connect_args = dict(kwargs.pop("connect_args", {}) or {})
    if busy_timeout_seconds is not None:
        connect_args["timeout"] = busy_timeout_seconds
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args=connect_args,
        **kwargs,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_wal(dbapi_connection, _connection_record):  # pragma: no cover - 驱动回调
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine
