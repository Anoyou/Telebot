"""一次性诊断脚本：dump 代理/账号/LLM 的实际 proxy_id 关联，
帮你判断"被谁用了"为什么返 0。"""
import asyncio

from sqlalchemy import select

from app.db.models.account import Account, Proxy
from app.db.models.command import LLMProvider
from app.db.session import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as db:
        proxies = (await db.execute(select(Proxy))).scalars().all()
        accounts = (await db.execute(
            select(Account.id, Account.proxy_id, Account.display_name)
        )).all()
        llms = (await db.execute(
            select(LLMProvider.id, LLMProvider.proxy_id, LLMProvider.name)
        )).all()

        print("=== Proxies ===")
        for p in proxies:
            print(f"  #{p.id} {p.type}://{p.host}:{p.port}")
        print()
        print("=== Accounts (id, proxy_id, name) ===")
        for a in accounts:
            mark = "" if a[1] is not None else " (DIRECT)"
            print(f"  #{a[0]} -> proxy_id={a[1]}{mark}  {a[2] or ''}")
        print()
        print("=== LLM Providers (id, proxy_id, name) ===")
        for ll in llms:
            mark = "" if ll[1] is not None else " (DIRECT)"
            print(f"  #{ll[0]} -> proxy_id={ll[1]}{mark}  {ll[2] or ''}")

        # 反查每条代理的引用数
        print()
        print("=== Usage per proxy (manual count) ===")
        for p in proxies:
            n_acc = sum(1 for a in accounts if a[1] == p.id)
            n_llm = sum(1 for ll in llms if ll[1] == p.id)
            print(f"  proxy #{p.id} -> {n_acc} accounts, {n_llm} llm providers")


asyncio.run(main())
