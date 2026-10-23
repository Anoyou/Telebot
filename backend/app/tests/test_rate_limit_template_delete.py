"""删除风控模板的前置引用检查（template_in_use → 409）回归测试。

Account 真实模型含 SQLite 无法编译的 ARRAY 列，这里用同构轻量模型替换
service 模块里的 Account 引用（该查询只用到 id / template_id 两列）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import BigInteger, MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.models.rate_limit import RateLimitRule, RateLimitTemplate
from app.services import rate_limit_service as svc
from app.services.rate_limit_service import SCOPE_TEMPLATE


class _FakeAccountBase(DeclarativeBase):
    metadata = MetaData()


class FakeAccount(_FakeAccountBase):
    __tablename__ = "fake_account"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 独立 MetaData 下不声明 FK（SQLite 建表时解析不到外部的 rate_limit_template 表），
    # 服务查询只用到本列的值。
    template_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "referencing",
    [True, False],
    ids=["referenced_rejected", "unreferenced_deleted"],
)
async def test_delete_template_reference_guard(monkeypatch, referencing: bool) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:

            def _create(sync_conn) -> None:
                RateLimitTemplate.__table__.create(sync_conn, checkfirst=True)
                RateLimitRule.__table__.create(sync_conn, checkfirst=True)
                FakeAccount.__table__.create(sync_conn, checkfirst=True)

            await conn.run_sync(_create)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(svc, "Account", FakeAccount)
        async with session_factory() as db:
            tpl = RateLimitTemplate(id=7, name="tpl", is_default=False)
            # SQLite 上 BigInteger 主键无自增，统一显式给 id。
            rule = RateLimitRule(
                id=101, scope=SCOPE_TEMPLATE, scope_id=7, action="send_message", policy="queue"
            )
            db.add(tpl)
            db.add(rule)
            if referencing:
                db.add(FakeAccount(id=1, template_id=7))
                db.add(FakeAccount(id=2, template_id=7))
            await db.commit()

            if referencing:
                with pytest.raises(svc.RateLimitTemplateInUse) as exc_info:
                    await svc.delete_template(db, 7)
                assert exc_info.value.account_ids == [1, 2]
                assert await db.get(RateLimitTemplate, 7) is not None
                assert await db.get(RateLimitRule, rule.id) is not None
            else:
                assert await svc.delete_template(db, 7) is True
                assert await db.get(RateLimitTemplate, 7) is None
                assert await db.get(RateLimitRule, rule.id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_template_missing_returns_false() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: RateLimitTemplate.__table__.create(
                    sync_conn, checkfirst=True
                )
            )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            assert await svc.delete_template(db, 404) is False
    finally:
        await engine.dispose()
