"""密钥池 CRUD：服务层（sqlite 内存库）与 API 路由层（审计 / 通知 / 不泄漏明文）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, Integer, MetaData, Table, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import crypto
from app.api import commands as commands_api
from app.db.models.command import LLMProvider, LLMProviderKey
from app.schemas.command import (
    LLMProviderCreate,
    LLMProviderKeyCreate,
    LLMProviderKeyUpdate,
    LLMProviderUpdate,
)
from app.services import command_service, llm_key_pool

SECRET = "sk-pool-secret-value-ABCD"


@pytest.fixture(autouse=True)
def _reset_pool_state(monkeypatch) -> None:
    llm_key_pool.reset_state_for_tests()
    monkeypatch.setattr(llm_key_pool, "load_daily_usage", AsyncMock(return_value={}))
    monkeypatch.setattr(crypto, "encrypt_str", lambda value: f"enc::{value}")
    monkeypatch.setattr(command_service, "encrypt_str", lambda value: f"enc::{value}")
    monkeypatch.setattr(command_service, "_validate_proxy_for_llm", AsyncMock(return_value=None))


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # SQLite 只对 INTEGER PRIMARY KEY 自增，模型里的 BigInteger 主键需在建表副本上换成 Integer。
    metadata = MetaData()
    proxy = Table("proxy", metadata, Column("id", Integer, primary_key=True))
    tables = [LLMProvider.__table__.to_metadata(metadata), LLMProviderKey.__table__.to_metadata(metadata)]
    for table in tables:
        table.c.id.type = Integer()
    async with engine.begin() as conn:
        for table in [proxy, *tables]:
            await conn.run_sync(table.create)
        await conn.execute(text("PRAGMA foreign_keys=ON"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _make_provider(db, **overrides) -> int:
    payload = LLMProviderCreate(
        name=overrides.pop("name", "pool-provider"),
        provider="openai",
        api_key=overrides.pop("api_key", "sk-main-key-0000"),
        base_url="https://api.example.com/v1",
        default_model="gpt-4o-mini",
        **overrides,
    )
    out = await command_service.create_provider(db, payload)
    return out.id


@pytest.mark.asyncio
async def test_create_list_update_delete_key_never_exposes_plaintext(session_factory) -> None:
    async with session_factory() as db:
        pid = await _make_provider(db)
        created = await command_service.create_provider_key(
            db, pid, LLMProviderKeyCreate(label="k-a", api_key=SECRET, priority=5)
        )
        await db.commit()

        assert created.key_hint == "ABCD"
        assert created.label == "k-a"
        assert created.priority == 5
        assert SECRET not in created.model_dump_json()

        listing = await command_service.list_provider_keys(db, pid)
        assert listing.has_main_key is True
        assert listing.key_strategy == "order"
        assert [k.label for k in listing.keys] == ["k-a"]
        assert SECRET not in listing.model_dump_json()

        db.expire_all()
        provider_out = next(p for p in await command_service.list_providers(db) if p.id == pid)
        assert [k.label for k in provider_out.pool_keys] == ["k-a"]
        assert provider_out.pool_keys[0].key_hint == "ABCD"
        assert SECRET not in provider_out.model_dump_json()

        row = (await db.execute(select(LLMProviderKey))).scalar_one()
        assert row.api_key_enc == f"enc::{SECRET}"

        updated = await command_service.update_provider_key(
            db,
            pid,
            created.id,
            LLMProviderKeyUpdate(api_key="sk-new-secret-WXYZ", enabled=False, daily_request_limit=100),
        )
        await db.commit()
        assert updated.key_hint == "WXYZ"
        assert updated.enabled is False
        assert updated.daily_request_limit == 100
        assert "sk-new-secret" not in updated.model_dump_json()

        short = await command_service.create_provider_key(
            db, pid, LLMProviderKeyCreate(label="short", api_key="abc1234")
        )
        assert short.key_hint is None

        await command_service.delete_provider_key(db, pid, created.id)
        await db.commit()
        remaining = await command_service.list_provider_keys(db, pid)
        assert [k.label for k in remaining.keys] == ["short"]


@pytest.mark.asyncio
async def test_key_strategy_persists_through_provider_update(session_factory) -> None:
    async with session_factory() as db:
        pid = await _make_provider(db, key_strategy="rotate")
        out = next(p for p in await command_service.list_providers(db) if p.id == pid)
        assert out.key_strategy == "rotate"
        updated = await command_service.update_provider(db, pid, LLMProviderUpdate(key_strategy="least_used"))
        assert updated.key_strategy == "least_used"


@pytest.mark.asyncio
async def test_create_key_rejects_conflicts_and_wrong_provider(session_factory) -> None:
    async with session_factory() as db:
        pid = await _make_provider(db)
        await command_service.create_provider_key(db, pid, LLMProviderKeyCreate(label="dup", api_key=SECRET))
        with pytest.raises(HTTPException) as conflict:
            await command_service.create_provider_key(
                db, pid, LLMProviderKeyCreate(label="dup", api_key=SECRET)
            )
        assert conflict.value.status_code == 409
        assert conflict.value.detail["code"] == "LLM_PROVIDER_KEY_LABEL_CONFLICT"

        with pytest.raises(HTTPException) as missing:
            await command_service.get_provider_key_row(db, pid + 1, 1)
        assert missing.value.status_code == 404

        gateway_pid = await _make_provider(db, name="gateway")
        (await command_service.get_provider_row(db, gateway_pid)).execution_backend = "codex_gateway"
        await db.flush()
        with pytest.raises(HTTPException) as unsupported:
            await command_service.create_provider_key(
                db, gateway_pid, LLMProviderKeyCreate(label="x", api_key=SECRET)
            )
        assert unsupported.value.detail["code"] == "LLM_PROVIDER_KEY_UNSUPPORTED"


@pytest.mark.asyncio
async def test_create_key_enforces_pool_limit(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(command_service, "_MAX_POOL_KEYS_PER_PROVIDER", 2)
    async with session_factory() as db:
        pid = await _make_provider(db)
        for idx in range(2):
            await command_service.create_provider_key(
                db, pid, LLMProviderKeyCreate(label=f"k-{idx}", api_key=SECRET)
            )
        with pytest.raises(HTTPException) as limit:
            await command_service.create_provider_key(
                db, pid, LLMProviderKeyCreate(label="k-x", api_key=SECRET)
            )
        assert limit.value.detail["code"] == "LLM_PROVIDER_KEY_LIMIT"


@pytest.mark.asyncio
async def test_deleting_provider_cascades_pool_keys(session_factory) -> None:
    async with session_factory() as db:
        pid = await _make_provider(db)
        await command_service.create_provider_key(db, pid, LLMProviderKeyCreate(label="k-a", api_key=SECRET))
        await db.commit()
        await command_service.delete_provider(db, pid)
        await db.commit()
        assert (await db.execute(select(LLMProviderKey))).scalars().all() == []


@pytest.mark.asyncio
async def test_api_create_key_audits_without_secret_and_notifies(monkeypatch) -> None:
    monkeypatch.setattr(commands_api, "_require_ai_enabled", AsyncMock(return_value=None))
    out = SimpleNamespace(id=11, label="k-a", enabled=True, priority=100)
    create = AsyncMock(return_value=out)
    monkeypatch.setattr(commands_api.command_service, "create_provider_key", create)
    monkeypatch.setattr(commands_api.command_service, "list_all_account_ids", AsyncMock(return_value=[1, 2]))
    notify = AsyncMock(return_value=None)
    monkeypatch.setattr(commands_api.command_service, "notify_reload", notify)
    audit_write = AsyncMock(return_value=None)
    monkeypatch.setattr(commands_api.audit, "write", audit_write)
    db = AsyncMock()

    payload = LLMProviderKeyCreate(label="k-a", api_key=SECRET)
    result = await commands_api.create_provider_key(pid=7, payload=payload, db=db, user=SimpleNamespace(id=3))

    assert result is out
    assert SECRET not in str(audit_write.await_args)
    assert audit_write.await_args.args[2] == "llm_provider.key_create"
    db.commit.assert_awaited_once_with()
    notify.assert_awaited_once_with([1, 2])


@pytest.mark.asyncio
async def test_api_update_key_marks_api_key_changed_without_value(monkeypatch) -> None:
    monkeypatch.setattr(commands_api, "_require_ai_enabled", AsyncMock(return_value=None))
    out = SimpleNamespace(id=11)
    monkeypatch.setattr(commands_api.command_service, "update_provider_key", AsyncMock(return_value=out))
    monkeypatch.setattr(commands_api.command_service, "list_all_account_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(commands_api.command_service, "notify_reload", AsyncMock(return_value=None))
    audit_write = AsyncMock(return_value=None)
    monkeypatch.setattr(commands_api.audit, "write", audit_write)
    db = AsyncMock()

    payload = LLMProviderKeyUpdate(api_key=SECRET, notes="private note", priority=3)
    await commands_api.update_provider_key(
        pid=7, key_id=11, payload=payload, db=db, user=SimpleNamespace(id=3)
    )

    detail = audit_write.await_args.kwargs["detail"]
    assert detail == {"key_id": 11, "priority": 3, "api_key_changed": True}
    assert SECRET not in str(audit_write.await_args)


@pytest.mark.asyncio
async def test_api_create_key_rolls_back_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(commands_api, "_require_ai_enabled", AsyncMock(return_value=None))
    monkeypatch.setattr(
        commands_api.command_service,
        "create_provider_key",
        AsyncMock(side_effect=HTTPException(status_code=409, detail={"code": "X"})),
    )
    notify = AsyncMock(return_value=None)
    monkeypatch.setattr(commands_api.command_service, "notify_reload", notify)
    db = AsyncMock()

    with pytest.raises(HTTPException):
        await commands_api.create_provider_key(
            pid=7,
            payload=LLMProviderKeyCreate(label="k", api_key=SECRET),
            db=db,
            user=SimpleNamespace(id=3),
        )
    db.rollback.assert_awaited_once_with()
    db.commit.assert_not_awaited()
    notify.assert_not_awaited()
