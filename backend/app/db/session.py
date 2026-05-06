"""DB session 依赖。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from .base import AsyncSessionLocal


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个事务作用域的 session。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
