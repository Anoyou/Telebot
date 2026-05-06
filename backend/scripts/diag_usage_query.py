"""直接调用 proxy_usage handler，看它实际返回什么——
绕过前端 / 鉴权，定位"为什么返 0"。"""
import asyncio

from sqlalchemy import select

from app.db.models.account import Account
from app.db.models.command import LLMProvider
from app.db.session import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as db:
        # 直接复制 proxy_usage 端点里的 SQL，看返什么
        pid = 1
        acc_rows = (await db.execute(
            select(Account.id, Account.display_name, Account.phone)
            .where(Account.proxy_id == pid)
            .order_by(Account.id)
        )).all()
        llm_rows = (await db.execute(
            select(LLMProvider.id, LLMProvider.name, LLMProvider.default_model)
            .where(LLMProvider.proxy_id == pid)
            .order_by(LLMProvider.id)
        )).all()
        print(f"pid={pid}")
        print(f"  accounts SQL returned {len(acc_rows)} rows:")
        for r in acc_rows:
            print(f"    {r}")
        print(f"  llm_providers SQL returned {len(llm_rows)} rows:")
        for r in llm_rows:
            print(f"    {r}")


asyncio.run(main())
