"""第 20 轮：系统级上下文（未绑定账号）下 Agent 主动查账号。

用户在 App 里问“管理 bot 我设置的是哪个”，会话未关联账号，关键词路由只披露
account_bots 域（都要求 account_id），模型直接让用户报账号 ID、工具调用 0 次。
"""

from __future__ import annotations

import pytest
from sqlalchemy import ARRAY as GenericArray
from sqlalchemy.dialects.postgresql import ARRAY as PgArray
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.crypto import encrypt_bytes, encrypt_str
from app.db.base import Base
from app.db.models.account import Account
from app.db.models.account_bot import AccountBot, AccountBotUser
from app.services.system_agent.context import ToolContext
from app.services.system_agent.prompts import build_system_prompt
from app.services.system_agent.registry import get_registry
from app.services.system_agent.skills.catalog import BUILTIN_SKILLS
from app.services.system_agent.skills.registry import SkillRegistry
from app.services.system_agent.tool_routing import (
    ensure_account_lookup_tool,
    route_locally,
    select_tool_specs,
)
from app.services.system_agent.tools import account_bots as account_bot_tools

SECRET_TOKEN = "123456789:AAFakeTokenForAccountScopeTest_xyz"


def _prompt(account_id: int | None) -> str:
    return build_system_prompt(
        timezone_name="Asia/Shanghai",
        channel="web",
        role="admin",
        account_id=account_id,
        version="test",
        agent_enabled=True,
        ai_enabled=True,
        command_prefix="/",
    )


def test_prompt_has_system_scope_account_lookup_rule() -> None:
    prompt = _prompt(None)
    assert "当前账号上下文：系统级（未绑定单一账号）" in prompt
    assert "先调用 accounts.list" in prompt
    assert "account_bots.list_all" in prompt
    assert "只有 1 个账号就直接用它" in prompt
    assert "逐个查询并按“账号名（#ID）：结果”汇总" in prompt
    assert "列出账号让用户选" in prompt
    assert "不要直接拒绝" in prompt


def test_list_all_is_registered_read_only_without_account_id() -> None:
    specs = {spec.name: spec for spec in get_registry().list_all()}
    spec = specs["account_bots.list_all"]
    assert spec.read_only is True
    assert spec.min_role == "viewer"
    assert "account_id" not in (spec.input_schema.get("required") or [])
    assert "account_id" not in spec.input_schema["properties"]
    skill = next(skill for skill in BUILTIN_SKILLS if skill.name == "account-bots")
    assert skill.allowed_tools[0] == "account_bots.list_all"


def _turn_tools(text: str, *, account_id: int | None, channel: str = "web") -> list[str]:
    all_specs = get_registry().list_all()
    available = {spec.name.split(".", 1)[0] for spec in all_specs}
    route = route_locally(text, available=available)
    assert route is not None
    routed = select_tool_specs(all_specs, route)
    skills = SkillRegistry(BUILTIN_SKILLS)
    narrowed = skills.narrow_tools(routed, skills.select(route))
    final = ensure_account_lookup_tool(
        narrowed, all_specs, account_id=account_id, channel=channel
    )
    return [spec.name for spec in final]


def test_system_scope_management_bot_question_exposes_lookup_tools() -> None:
    names = _turn_tools("管理 bot 我设置的是哪个", account_id=None)
    assert "account_bots.list_all" in names
    assert "accounts.list" in names


def test_bound_account_or_bot_channel_does_not_add_accounts_list() -> None:
    assert "accounts.list" not in _turn_tools("管理 bot 我设置的是哪个", account_id=3)
    assert "accounts.list" not in _turn_tools(
        "管理 bot 我设置的是哪个", account_id=None, channel="bot"
    )


def test_lookup_tool_not_added_when_no_tool_needs_account_id() -> None:
    all_specs = get_registry().list_all()
    no_account = [spec for spec in all_specs if spec.name == "account_bots.list_all"]
    assert ensure_account_lookup_tool(
        no_account, all_specs, account_id=None, channel="web"
    ) == no_account


async def _seed(db: AsyncSession) -> None:
    good = {
        "api_id_enc": encrypt_str("1"),
        "api_hash_enc": encrypt_str("h"),
        "session_enc": encrypt_bytes(b"s"),
    }
    db.add_all(
        [
            Account(id=1, phone="8613800001234", display_name="客服A", tg_username="alice", status="active", **good),
            Account(id=2, phone="8613900005678", display_name="客服B", tg_username="bob", status="active", **good),
            Account(id=3, phone="447700900111", display_name=None, tg_username=None, status="paused", **good),
        ]
    )
    db.add_all(
        [
            AccountBot(
                id=1,
                account_id=1,
                bot_token_enc=encrypt_str(SECRET_TOKEN),
                enabled=True,
                status="running",
                username="alice_admin_bot",
                remote_plugin_policy={},
            ),
            AccountBot(
                id=2,
                account_id=2,
                bot_token_enc=None,
                enabled=False,
                status="disabled",
                username=None,
                remote_plugin_policy={},
            ),
        ]
    )
    db.add_all(
        [
            AccountBotUser(id=1, account_id=1, tg_user_id=1001, role="admin", enabled=True),
            AccountBotUser(id=2, account_id=1, tg_user_id=1002, role="viewer", enabled=True),
            AccountBotUser(id=3, account_id=1, tg_user_id=1003, role="viewer", enabled=False),
        ]
    )
    await db.commit()


@pytest.fixture
async def seeded_db():
    from sqlalchemy.ext.compiler import compiles

    @compiles(GenericArray, "sqlite")
    @compiles(PgArray, "sqlite")
    def _array_as_json(type_, compiler, **kw):  # noqa: ARG001
        return "JSON"

    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        Base.metadata.tables[name]
        for name in ("account", "account_bot", "account_bot_user")
    ]
    # account 表的外键依赖（代理/设备档案/风控模板）一并建，避免 sqlite 建表失败
    for table in list(tables):
        for fk in table.foreign_keys:
            ref = fk.column.table
            if ref not in tables:
                tables.insert(0, ref)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    async with AsyncSession(engine) as db:
        await _seed(db)
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_all_returns_every_account_in_system_scope(seeded_db) -> None:
    result = await account_bot_tools.list_all(
        ToolContext(db=seeded_db, channel="web", role="viewer", account_id=None), {}
    )
    assert result["count"] == 3
    assert result["bound_count"] == 1
    assert result["enabled_count"] == 1
    by_id = {item["account_id"]: item for item in result["accounts"]}
    assert by_id[1]["account_name"] == "客服A"
    assert by_id[1]["bot_username"] == "alice_admin_bot"
    assert by_id[1]["bot_link"] == "https://t.me/alice_admin_bot"
    assert by_id[1]["bound"] is True and by_id[1]["enabled"] is True
    assert by_id[1]["authorized_users"] == 2
    assert by_id[2]["configured"] is True and by_id[2]["bound"] is False
    assert by_id[2]["bot_username"] is None
    assert by_id[3]["configured"] is False
    assert by_id[3]["status"] == "disabled"
    assert by_id[3]["account_name"] == "账号 #3"
    text = repr(result)
    assert SECRET_TOKEN not in text
    assert "token_enc" not in text
    assert "8613800001234" not in text


@pytest.mark.asyncio
async def test_list_all_bot_channel_only_returns_bound_account(seeded_db) -> None:
    result = await account_bot_tools.list_all(
        ToolContext(db=seeded_db, channel="bot", role="viewer", account_id=2), {}
    )
    assert result["count"] == 1
    assert result["accounts"][0]["account_id"] == 2
