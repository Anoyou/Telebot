"""System Agent 会话/配置/消息编排。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.system import SystemSetting
from app.db.models.system_agent import (
    CHANNEL_BOT,
    CHANNEL_WEB,
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_TOOL,
    MESSAGE_ROLE_USER,
    MESSAGE_RUN_COMPLETED,
    MESSAGE_RUN_FAILED,
    MESSAGE_RUN_PENDING,
    MESSAGE_RUN_SUCCEEDED,
    SESSION_ORIGIN_INTERACTIVE,
    SESSION_ORIGIN_SCHEDULED,
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_ARCHIVED,
    SystemAgentMessage,
    SystemAgentRunEvent,
    SystemAgentSession,
)
from app.services.system_agent.config import (
    normalize_config,
    resolve_agent_providers,
    resolve_fixed_provider,
    save_config,
    tools_model_for_dto,
    tools_models_for_dto,
)
from app.services.system_agent.prompts import build_system_prompt, session_title_from_message
from app.services.system_agent.secrets import decrypt_secret_map
from app.services.system_agent.service import SystemAgentService


@pytest.fixture
async def agent_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # 最小 FK 目标表，避免创建完整 WebUser / Account 约束
        await conn.execute(text("CREATE TABLE web_user (id INTEGER PRIMARY KEY)"))
        await conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY)"))
        await conn.run_sync(SystemSetting.__table__.create)
        await conn.run_sync(SystemAgentSession.__table__.create)
        await conn.run_sync(SystemAgentMessage.__table__.create)
        await conn.run_sync(SystemAgentRunEvent.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def test_normalize_config_defaults_and_clamps() -> None:
    cfg = normalize_config(None)
    assert cfg["enabled"] is False
    assert cfg["provider_id"] is None
    assert cfg["require_tool_approval"] is False
    assert cfg["max_steps"] >= 1
    over = normalize_config(
        {
            "enabled": 1,
            "provider_id": "12",
            "model": "  gpt-x  ",
            "fallback_provider_ids": [12, "13", 13, 0, "bad"],
            "require_tool_approval": True,
            "max_steps": 999,
            "max_tool_calls": 999,
            "session_token_limit": 10,
        }
    )
    assert over["enabled"] is True
    assert over["provider_id"] == 12
    assert over["model"] == "gpt-x"
    assert over["fallback_provider_ids"] == [13]
    assert over["require_tool_approval"] is True
    assert over["max_steps"] == 16
    assert over["max_tool_calls"] == 64
    assert over["session_token_limit"] == 1024

    unlimited = normalize_config({"session_token_limit": 0})
    assert unlimited["session_token_limit"] == 0


def test_session_title_from_message() -> None:
    assert session_title_from_message("  交互里\n有哪些规则？  ") == "交互里 有哪些规则？"
    assert len(session_title_from_message("a" * 100)) == 30
    assert session_title_from_message("") == "新对话"


def test_system_prompt_includes_bot_identity_context() -> None:
    prompt = build_system_prompt(
        timezone_name="Asia/Shanghai",
        channel=CHANNEL_BOT,
        role="admin",
        account_id=1,
        bot_tg_user_id=1682400007,
        version="0.70.4",
        agent_enabled=True,
        ai_enabled=True,
        command_prefix="/",
    )
    assert "account_id=1" in prompt
    assert "Telegram 管理 Bot 触发者 ID：1682400007" in prompt
    assert "直接回答" in prompt
    assert "source.search" not in prompt
    assert "web.search" not in prompt


def test_tools_model_for_dto_requires_tools_support() -> None:
    dto = SimpleNamespace(
        enabled_model_ids=lambda: ["m1", "m2"],
        has_model_list=lambda: True,
        pick_enabled_model=lambda: "m1",
        capabilities_for_model=lambda mid: SimpleNamespace(tools=(mid == "m2")),
    )
    assert tools_model_for_dto(dto) == "m2"
    assert tools_model_for_dto(dto, "m1") is None
    assert tools_model_for_dto(dto, "m2") == "m2"
    assert tools_model_for_dto(dto, "missing") is None
    assert tools_models_for_dto(dto, "m2") == ["m2"]


@pytest.mark.asyncio
async def test_resolve_fixed_provider_disabled(agent_db) -> None:
    async with agent_db() as db:
        db.add(SystemSetting(key="system_agent_config", value={"enabled": False}))
        await db.commit()
        dto, err = await resolve_fixed_provider(db)
        assert dto is None
        assert "未启用" in err


@pytest.mark.asyncio
async def test_resolve_agent_providers_uses_fallback_allowlist() -> None:
    def row(provider_id: int, name: str, *, supports_tools: bool = True):
        return SimpleNamespace(
            id=provider_id,
            name=name,
            provider="openai",
            api_format="responses",
            base_url="https://example.invalid/v1",
            default_model=f"model-{provider_id}",
            api_key_enc="encrypted",
            models=[
                {
                    "id": f"model-{provider_id}",
                    "enabled": True,
                    "supports_tools": supports_tools,
                }
            ],
        )

    rows = [
        row(1, "primary"),
        row(2, "allowed"),
        row(3, "not-selected"),
        row(4, "no-tools", supports_tools=False),
    ]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    class _DB:
        async def execute(self, _query):
            return _Result()

    resolved = await resolve_agent_providers(
        _DB(),  # type: ignore[arg-type]
        {
            "enabled": True,
            "provider_id": 1,
            "model": "model-1",
            "fallback_provider_ids": [2, 4],
        },
    )

    assert not isinstance(resolved, str)
    assert list(resolved.providers) == [1, 2]


@pytest.mark.asyncio
async def test_session_crud_and_ownership(agent_db) -> None:
    svc = SystemAgentService()
    async with agent_db() as db:
        s1 = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1, title="t1")
        s2 = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=2, title="t2")
        bot = await svc.create_session(
            db,
            channel=CHANNEL_BOT,
            bot_tg_user_id=1682400007,
            account_id=3,
            title="Telegram",
        )
        await db.commit()

        rows = await svc.list_sessions(db, web_user_id=1)
        assert [r.id for r in rows] == [s1.id]

        visible_to_admin = await svc.list_sessions(
            db,
            web_user_id=1,
            include_bot_sessions=True,
        )
        assert {row.id for row in visible_to_admin} == {s1.id, bot.id}

        owned = await svc.get_session(db, s1.id, web_user_id=1)
        assert owned is not None
        foreign = await svc.get_session(db, s2.id, web_user_id=1)
        assert foreign is None
        visible_bot = await svc.get_session(
            db,
            bot.id,
            web_user_id=1,
            allow_bot_session=True,
        )
        assert visible_bot is bot

        await svc.update_session(db, s1, status=SESSION_STATUS_ARCHIVED, title="archived")
        await db.commit()
        assert s1.status == SESSION_STATUS_ARCHIVED
        assert s1.title == "archived"

        active = await svc.list_sessions(db, web_user_id=1, status=SESSION_STATUS_ACTIVE)
        assert active == []


@pytest.mark.asyncio
async def test_bot_session_requires_account(agent_db) -> None:
    svc = SystemAgentService()
    async with agent_db() as db:
        with pytest.raises(ValueError, match="account_id"):
            await svc.create_session(db, channel=CHANNEL_BOT, bot_tg_user_id=99)


@pytest.mark.asyncio
async def test_scheduled_session_not_reused_by_get_or_create(agent_db) -> None:
    """定时会话不进入 get_or_create_active_session 复用池。"""
    svc = SystemAgentService()
    async with agent_db() as db:
        scheduled = await svc.create_session(
            db,
            channel=CHANNEL_WEB,
            web_user_id=9,
            title="定时 · 巡检 · 07-25 09:00",
            origin=SESSION_ORIGIN_SCHEDULED,
        )
        await db.commit()
        assert scheduled.origin == SESSION_ORIGIN_SCHEDULED

        listed = await svc.list_sessions(db, web_user_id=9, origin=SESSION_ORIGIN_SCHEDULED)
        assert [r.id for r in listed] == [scheduled.id]

        interactive = await svc.get_or_create_active_session(
            db, channel=CHANNEL_WEB, web_user_id=9
        )
        assert interactive.id != scheduled.id
        assert interactive.origin == SESSION_ORIGIN_INTERACTIVE


@pytest.mark.asyncio
async def test_get_or_create_and_clear_messages(agent_db) -> None:
    svc = SystemAgentService()
    async with agent_db() as db:
        session = await svc.get_or_create_active_session(db, channel=CHANNEL_WEB, web_user_id=7)
        again = await svc.get_or_create_active_session(db, channel=CHANNEL_WEB, web_user_id=7)
        assert session.id == again.id

        db.add(
            SystemAgentMessage(
                session_id=session.id,
                role=MESSAGE_ROLE_USER,
                content={"text": "hello"},
            )
        )
        session.memory_summary = "old summary"
        session.memory_state = {"last_domains": ["logs"]}
        await db.flush()
        msgs = await svc.list_messages(db, session.id)
        assert len(msgs) == 1
        deleted = await svc.clear_messages(db, session)
        assert deleted == 1
        assert await svc.list_messages(db, session.id) == []
        assert session.memory_summary == ""
        assert session.memory_state == {}


@pytest.mark.asyncio
async def test_stream_message_persists_redacted_user_and_assistant(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def fake_stream(*_a, **_k):
        yield {
            "type": "assistant_message",
            "content": "查询完成",
            "usage": {"total_tokens": 3},
        }
        yield {
            "type": "tool_finished",
            "tool_name": "system.get_context",
            "call_id": "c1",
            "is_error": False,
            "result_summary": {"ok": True},
        }
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        events: list[dict[str, Any]] = []
        async for ev in svc.stream_message(
            db,
            session=session,
            text="系统版本是多少？",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            events.append(ev)
        await db.commit()

        assert any(e.get("type") == "assistant_message" for e in events)
        msgs = await svc.list_messages(db, session.id)
        roles = [m.role for m in msgs]
        assert MESSAGE_ROLE_USER in roles
        assert "assistant" in roles
        assert "tool" in roles
        assert session.title == "系统版本是多少？"


@pytest.mark.parametrize(
    "request_text",
    (
        "请设置兼容请求头 X-Tenant-ID 为 opaque-header-secret",
        "Please set HTTP header X-Tenant-ID to opaque-header-secret",
        "Set custom header X-Tenant-ID=opaque-header-secret",
    ),
)
@pytest.mark.asyncio
async def test_request_header_configuration_never_reaches_model_or_history(
    agent_db,
    monkeypatch,
    request_text: str,
) -> None:
    svc = SystemAgentService()
    secret = "opaque-header-secret"
    model_inputs: list[str] = []

    async def fake_stream(*_args, **kwargs):
        model_inputs.append(str(kwargs["user_text"]))
        yield {"type": "assistant_message", "content": "请使用 Provider 设置页", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text=request_text,
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        messages = await svc.list_messages(db, session.id)
        assert model_inputs and secret not in model_inputs[0]
        assert "Provider 设置" in model_inputs[0]
        assert secret not in str(session.title)
        assert secret not in str(session.memory_state)
        assert secret not in str([message.content for message in messages])


@pytest.mark.asyncio
async def test_successful_turn_redacts_secret_from_persistent_memory(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

    async def fake_stream(*_args, **_kwargs):
        yield {"type": "route_selected", "domains": ["providers"]}
        yield {
            "type": "tool_finished",
            "tool_name": "providers.save",
            "call_id": "c-secret",
            "is_error": False,
            "result_summary": {"echo": secret},
        }
        yield {
            "type": "assistant_message",
            "content": f"已处理 {secret}",
            "usage": {},
        }
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        events = [
            event
            async for event in svc.stream_message(
                db,
                session=session,
                text=f"添加 Provider，key={secret}",
                role="admin",
                channel=CHANNEL_WEB,
                web_user_id=1,
            )
        ]

        assert secret not in session.memory_summary
        assert secret not in str(session.memory_state)
        assert "REDACTED" in str(session.memory_state)
        assert secret not in str(session.title)
        assistant = next(event for event in events if event.get("type") == "assistant_message")
        assert secret not in assistant["content"]
        assert "REDACTED" in assistant["content"]


@pytest.mark.asyncio
async def test_stream_message_commits_history_before_terminal_events(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def fake_stream(*_a, **_k):
        yield {"type": "run_started", "run_id": "r1"}
        yield {
            "type": "assistant_message",
            "content": "已完成",
            "reasoning": "先检查状态再汇总",
            "usage": {},
        }
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        await db.commit()
        stream = svc.stream_message(
            db,
            session=session,
            text="断线后也要保留",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        )

        first = await anext(stream)
        assert first["type"] == "run_started"
        async with agent_db() as observer:
            roles = [m.role for m in await svc.list_messages(observer, session.id)]
            assert roles == [MESSAGE_ROLE_USER]

        second = await anext(stream)
        assert second["type"] == "assistant_message"
        async with agent_db() as observer:
            saved_messages = await svc.list_messages(observer, session.id)
            roles = [m.role for m in saved_messages]
            assert roles == [MESSAGE_ROLE_USER, "assistant"]
            assert saved_messages[-1].content["reasoning"] == "先检查状态再汇总"
            assert second["message_id"] == saved_messages[-1].id

        await stream.aclose()


@pytest.mark.asyncio
async def test_failed_turn_is_marked_and_excluded_from_next_history(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()
    histories: list[list[SystemAgentMessage]] = []
    call_count = 0

    async def fake_stream(*_args, **kwargs):
        nonlocal call_count
        histories.append(list(kwargs.get("history_messages") or []))
        call_count += 1
        if call_count == 1:
            yield {"type": "error", "code": "UPSTREAM_503", "message": "上游暂时不可用"}
            yield {"type": "done", "ok": False}
            return
        yield {"type": "assistant_message", "content": "第二轮成功", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="第一轮失败",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        first_messages = await svc.list_messages(db, session.id)
        assert len(first_messages) == 1
        assert first_messages[0].run_status == MESSAGE_RUN_FAILED
        assert first_messages[0].error_code == "UPSTREAM_503"

        async for _event in svc.stream_message(
            db,
            session=session,
            text="第二轮",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        assert histories == [[], []]
        rows = await svc.list_messages(db, session.id)
        assert rows[-2].run_status == MESSAGE_RUN_SUCCEEDED
        assert session.memory_state["last_result"] == "第二轮成功"


@pytest.mark.asyncio
async def test_provider_switch_failure_is_persisted_for_confirmation(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def fake_stream(*_args, **_kwargs):
        yield {
            "type": "error",
            "code": "AGENT_PROVIDER_SWITCH_REQUIRED",
            "message": "是否切换 Provider？",
            "provider_switch": {
                "from_provider_name": "primary",
                "candidates": [{"provider_id": 2, "provider_name": "fallback", "model": "model-b"}],
            },
        }
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="查询定时任务",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        assert message.run_status == MESSAGE_RUN_FAILED
        assert message.error_code == "AGENT_PROVIDER_SWITCH_REQUIRED"
        assert message.usage == {
            "provider_switch": {
                "from_provider_name": "primary",
                "candidates": [{"provider_id": 2, "provider_name": "fallback", "model": "model-b"}],
            }
        }


@pytest.mark.asyncio
async def test_tool_approval_failure_is_persisted_for_retry(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def fake_stream(*_args, **_kwargs):
        yield {
            "type": "error",
            "code": "AGENT_TOOL_APPROVAL_REQUIRED",
            "message": "本轮需要调用系统工具，请批准后继续。",
            "tool_approval": {
                "domains": ["scheduler"],
                "tools": [
                    {
                        "call_id": "call-secret",
                        "name": "scheduler.list",
                        "description": "查看定时任务",
                        "read_only": True,
                        "risk": "normal",
                        "arguments": {"api_key": "opaque-secret-value"},
                    }
                ],
                "calls": [
                    {
                        "call_id": "call-secret",
                        "name": "scheduler.list",
                        "arguments": {"api_key": "opaque-secret-value"},
                    }
                ],
            },
        }
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="查询定时任务",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        assert message.error_code == "AGENT_TOOL_APPROVAL_REQUIRED"
        assert message.usage is not None
        assert message.usage["tool_approval"]["tools"][0]["name"] == "scheduler.list"
        assert "arguments" not in message.usage["tool_approval"]["tools"][0]
        assert "arguments" not in message.usage["tool_approval"]["calls"][0]
        assert "opaque-secret-value" not in str(message.usage)


@pytest.mark.asyncio
async def test_tool_approvals_accumulate_across_retries(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()
    attempts: list[list[str]] = []

    async def fake_stream(*_args, **kwargs):
        approved = sorted(kwargs.get("approved_tools") or [])
        attempts.append(approved)
        if not approved:
            yield {
                "type": "error",
                "code": "AGENT_TOOL_APPROVAL_REQUIRED",
                "message": "请批准读取日志",
                "tool_approval": {
                    "domains": ["logs"],
                    "calls": [{"call_id": "call-1", "name": "logs.recent", "arguments": {}}],
                    "tools": [{"call_id": "call-1", "name": "logs.recent", "arguments": {}}],
                },
            }
            yield {"type": "done", "ok": False}
            return
        if approved == ["logs.recent"]:
            yield {
                "type": "error",
                "code": "AGENT_TOOL_APPROVAL_REQUIRED",
                "message": "请批准搜索日志",
                "tool_approval": {
                    "domains": ["logs"],
                    "calls": [{"call_id": "call-2", "name": "logs.search_errors", "arguments": {}}],
                    "tools": [{"call_id": "call-2", "name": "logs.search_errors", "arguments": {}}],
                },
            }
            yield {"type": "done", "ok": False}
            return
        yield {"type": "assistant_message", "content": "处理完成", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="排查日志",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        async for _event in svc.stream_message(
            db,
            session=session,
            text="",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            retry_message=message,
            approved_tools=["logs.recent"],
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        assert message.usage["approved_tools"] == ["logs.recent"]
        async for _event in svc.stream_message(
            db,
            session=session,
            text="",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            retry_message=message,
            approved_tools=["logs.search_errors"],
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        assert message.run_status == MESSAGE_RUN_SUCCEEDED
        assert attempts == [[], ["logs.recent"], ["logs.recent", "logs.search_errors"]]


@pytest.mark.asyncio
async def test_tool_approval_retry_rejects_tools_outside_pending_set(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def fake_stream(*_args, **_kwargs):
        yield {
            "type": "error",
            "code": "AGENT_TOOL_APPROVAL_REQUIRED",
            "message": "请批准读取日志",
            "tool_approval": {
                "domains": ["logs"],
                "tools": [{"name": "logs.recent", "description": "读取日志"}],
            },
        }
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="读取日志",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        with pytest.raises(ValueError, match="批准工具不在当前待确认列表"):
            async for _event in svc.stream_message(
                db,
                session=session,
                text="",
                role="admin",
                channel=CHANNEL_WEB,
                web_user_id=1,
                retry_message=message,
                approved_tools=["settings.update"],
            ):
                pass


@pytest.mark.asyncio
async def test_failed_turn_persists_durable_run_id(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def fake_stream(*_args, **_kwargs):
        yield {"type": "error", "code": "UPSTREAM_FAILED", "message": "上游失败"}
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="测试失败轨迹",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            run_id="durable-failed-run",
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        assert message.run_status == MESSAGE_RUN_FAILED
        assert message.usage == {"run_id": "durable-failed-run"}


@pytest.mark.asyncio
async def test_provider_switch_and_tool_approval_context_are_persisted_together(
    agent_db, monkeypatch
) -> None:
    svc = SystemAgentService()

    async def fake_stream(*_args, **_kwargs):
        yield {
            "type": "error",
            "code": "AGENT_PROVIDER_SWITCH_REQUIRED",
            "message": "是否切换 Provider？",
            "provider_switch": {
                "from_provider_name": "primary",
                "candidates": [{"provider_id": 2, "provider_name": "fallback", "model": "model-b"}],
            },
            "tool_approval": {
                "domains": ["scheduler"],
                "tools": [
                    {
                        "name": "scheduler.list",
                        "description": "查看定时任务",
                        "read_only": True,
                        "risk": "normal",
                    }
                ],
            },
        }
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="查询定时任务",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        message = (await svc.list_messages(db, session.id))[0]
        assert message.usage is not None
        assert message.usage["provider_switch"]["candidates"][0]["provider_id"] == 2
        assert message.usage["tool_approval"]["tools"][0]["name"] == "scheduler.list"


@pytest.mark.asyncio
async def test_cancelled_stream_marks_message_retryable(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def cancelled_stream(*_args, **_kwargs):
        yield {"type": "run_started", "run_id": "cancelled"}
        raise asyncio.CancelledError

    monkeypatch.setattr(svc.runtime, "stream_turn", cancelled_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        stream = svc.stream_message(
            db,
            session=session,
            text="会被断开的请求",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        )
        assert (await anext(stream))["type"] == "run_started"
        with pytest.raises(asyncio.CancelledError):
            await anext(stream)

    async with agent_db() as observer:
        message = (await svc.list_messages(observer, session.id))[0]
        assert message.run_status == MESSAGE_RUN_FAILED
        assert message.error_code == "AGENT_STREAM_CANCELLED"


@pytest.mark.asyncio
async def test_reconcile_stale_pending_message(agent_db) -> None:
    svc = SystemAgentService()
    old = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        db.add(
            SystemAgentMessage(
                session_id=session.id,
                role=MESSAGE_ROLE_USER,
                content={"text": "旧请求"},
                usage={"run_started_at": old},
                run_status="pending",
            )
        )
        await db.commit()
        assert await svc.reconcile_stale_messages(db, session.id) == 1
        await db.commit()
        message = (await svc.list_messages(db, session.id))[0]
        assert message.run_status == MESSAGE_RUN_FAILED
        assert message.error_code == "AGENT_STREAM_INTERRUPTED"
        assert session.memory_state["failed_turn"] == {
            "message_id": message.id,
            "user_goal": "旧请求",
            "error_code": "AGENT_STREAM_INTERRUPTED",
        }


@pytest.mark.asyncio
async def test_reconcile_keeps_pending_message_within_runtime_window(agent_db) -> None:
    svc = SystemAgentService()
    still_running = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        db.add(
            SystemAgentMessage(
                session_id=session.id,
                role=MESSAGE_ROLE_USER,
                content={"text": "仍在运行的长请求"},
                usage={"run_started_at": still_running},
                run_status="pending",
            )
        )
        await db.commit()

        assert await svc.reconcile_stale_messages(db, session.id) == 0
        message = (await svc.list_messages(db, session.id))[0]
        assert message.run_status == MESSAGE_RUN_PENDING


@pytest.mark.asyncio
async def test_retry_reuses_failed_user_message(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def fail_stream(*_args, **_kwargs):
        yield {"type": "error", "code": "TIMEOUT", "message": "超时"}
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(svc.runtime, "stream_turn", fail_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="查询日志",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass
        failed = (await svc.list_messages(db, session.id))[0]
        original_id = failed.id

        async def success_stream(*_args, **_kwargs):
            yield {"type": "route_selected", "domains": ["logs"]}
            yield {"type": "assistant_message", "content": "已恢复", "usage": {}}
            yield {"type": "done", "ok": True}

        monkeypatch.setattr(svc.runtime, "stream_turn", success_stream)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            retry_message=failed,
        ):
            pass

        rows = await svc.list_messages(db, session.id)
        users = [row for row in rows if row.role == MESSAGE_ROLE_USER]
        assert len(users) == 1
        assert users[0].id == original_id
        assert users[0].run_status == MESSAGE_RUN_SUCCEEDED
        assert users[0].retry_count == 1
        assert users[0].error_message is None


@pytest.mark.asyncio
async def test_retry_reference_reuses_latest_failed_goal_and_clears_anchor(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()
    runtime_goals: list[str] = []
    call_count = 0

    async def fake_stream(*_args, **kwargs):
        nonlocal call_count
        runtime_goals.append(str(kwargs["user_text"]))
        call_count += 1
        if call_count == 1:
            yield {"type": "error", "code": "UPSTREAM_503", "message": "上游暂时不可用"}
            yield {"type": "done", "ok": False}
            return
        yield {"type": "route_selected", "domains": ["interaction"]}
        yield {"type": "assistant_message", "content": "当前有 2 条交互规则", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="交互里有哪些规则？",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        failed = (await svc.list_messages(db, session.id))[0]
        original_id = failed.id
        assert session.memory_state["failed_turn"] == {
            "message_id": original_id,
            "user_goal": "交互里有哪些规则？",
            "error_code": "UPSTREAM_503",
        }

        async for _event in svc.stream_message(
            db,
            session=session,
            text="重试",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        rows = await svc.list_messages(db, session.id)
        users = [row for row in rows if row.role == MESSAGE_ROLE_USER]
        assert runtime_goals == ["交互里有哪些规则？", "交互里有哪些规则？"]
        assert len(users) == 1
        assert users[0].id == original_id
        assert users[0].retry_count == 1
        assert users[0].run_status == MESSAGE_RUN_SUCCEEDED
        assert "failed_turn" not in session.memory_state


@pytest.mark.asyncio
async def test_retry_reference_without_failure_is_a_normal_message(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()
    runtime_goals: list[str] = []

    async def fake_stream(*_args, **kwargs):
        runtime_goals.append(str(kwargs["user_text"]))
        yield {"type": "assistant_message", "content": "没有可重试的任务", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="重试",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        users = [row for row in await svc.list_messages(db, session.id) if row.role == MESSAGE_ROLE_USER]
        assert runtime_goals == ["重试"]
        assert len(users) == 1
        assert users[0].retry_count == 0


@pytest.mark.asyncio
async def test_failed_secret_goal_anchor_and_retry_stay_redacted(agent_db, monkeypatch) -> None:
    """失败轮锚点与重试上下文都不能泄漏真实密钥（WP-B5 后改用占位符）。"""
    svc = SystemAgentService()
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    runtime_goals: list[str] = []

    async def fail_stream(*_args, **kwargs):
        runtime_goals.append(str(kwargs["user_text"]))
        yield {"type": "error", "code": "TIMEOUT", "message": "超时"}
        yield {"type": "done", "ok": False}

    monkeypatch.setattr(svc.runtime, "stream_turn", fail_stream)

    user_msg_id: int | None = None

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text=f"添加 Provider，key={secret}",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass
        row = await db.execute(text("SELECT id FROM system_agent_message WHERE role='user'"))
        user_msg_id = row.scalar_one()
        assert secret not in str(session.memory_state)
        assert secret not in str(session.secret_map_enc or "")
        assert secret not in runtime_goals[0]
        # 给模型的是占位符，失败锚点也保持占位符而不是 `[REDACTED]`
        assert "[[TP-CRED-1]]" in runtime_goals[0]
        assert "[[TP-CRED-1]]" in session.memory_state["failed_turn"]["user_goal"]
        # 落库的用户消息与失败锚点都保持占位符（持久化面不出现真实密钥）
        stored = await db.get(SystemAgentMessage, user_msg_id)
        assert stored is not None and "[[TP-CRED-1]]" in str(stored.content.get("text"))
        assert secret not in str(stored.content)
        # 映射可解密，本轮密钥确实注册过，供显示边界还原
        assert decrypt_secret_map(session.secret_map_enc) == {"[[TP-CRED-1]]": secret}

        async for _event in svc.stream_message(
            db,
            session=session,
            text="重新试试",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        assert secret not in runtime_goals[1]
        assert "[[TP-CRED-1]]" in runtime_goals[1]


@pytest.mark.asyncio
async def test_stale_concurrent_retry_cannot_execute_twice(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def success_stream(*_args, **_kwargs):
        yield {"type": "assistant_message", "content": "完成", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", success_stream)

    async with agent_db() as setup_db:
        session = await svc.create_session(
            setup_db,
            channel=CHANNEL_WEB,
            web_user_id=1,
        )
        message = SystemAgentMessage(
            session_id=session.id,
            role=MESSAGE_ROLE_USER,
            content={"text": "只允许重试一次"},
            run_status=MESSAGE_RUN_FAILED,
        )
        setup_db.add(message)
        await setup_db.commit()
        session_id = session.id
        message_id = message.id

    async with agent_db() as first_db, agent_db() as stale_db:
        first_session = await first_db.get(SystemAgentSession, session_id)
        first_message = await first_db.get(SystemAgentMessage, message_id)
        stale_session = await stale_db.get(SystemAgentSession, session_id)
        stale_message = await stale_db.get(SystemAgentMessage, message_id)
        assert first_session is not None and first_message is not None
        assert stale_session is not None and stale_message is not None

        async for _event in svc.stream_message(
            first_db,
            session=first_session,
            text="",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            retry_message=first_message,
        ):
            pass

        with pytest.raises(ValueError, match="已被重试|状态已变化"):
            async for _event in svc.stream_message(
                stale_db,
                session=stale_session,
                text="",
                role="admin",
                channel=CHANNEL_WEB,
                web_user_id=1,
                retry_message=stale_message,
            ):
                pass


@pytest.mark.asyncio
async def test_retry_with_explicit_text_sends_supplement_to_runtime(
    agent_db,
    monkeypatch,
) -> None:
    svc = SystemAgentService()
    runtime_goals: list[str] = []

    async def success_stream(*_args, **kwargs):
        runtime_goals.append(str(kwargs["user_text"]))
        yield {"type": "assistant_message", "content": "完成", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", success_stream)

    async with agent_db() as db:
        session = await svc.create_session(
            db,
            channel=CHANNEL_WEB,
            web_user_id=1,
        )
        message = SystemAgentMessage(
            session_id=session.id,
            role=MESSAGE_ROLE_USER,
            content={"text": "执行原任务"},
            run_status=MESSAGE_RUN_FAILED,
        )
        db.add(message)
        await db.commit()

        supplemented = "执行原任务\n\n补充说明：改用备用模型"
        async for _event in svc.stream_message(
            db,
            session=session,
            text=supplemented,
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            retry_message=message,
        ):
            pass

        assert runtime_goals == [supplemented]
        assert message.content == {"text": supplemented}


@pytest.mark.asyncio
async def test_unexpected_stream_crash_becomes_retryable_failed_turn(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()

    async def crash_stream(*_args, **_kwargs):
        yield {"type": "run_started", "run_id": "r-crash"}
        yield {"type": "assistant_message", "content": "未提交的答案", "usage": {}}
        raise RuntimeError("boom")

    monkeypatch.setattr(svc.runtime, "stream_turn", crash_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        events = [
            event
            async for event in svc.stream_message(
                db,
                session=session,
                text="会崩溃的一轮",
                role="admin",
                channel=CHANNEL_WEB,
                web_user_id=1,
            )
        ]

        assert events[0]["type"] == "run_started"
        assert all(event.get("type") != "assistant_message" for event in events)
        assert any(event.get("code") == "AGENT_STREAM_FAILED" for event in events)
        assert events[-1] == {"type": "done", "ok": False, "session_id": session.id}
        row = (await svc.list_messages(db, session.id))[0]
        assert row.run_status == MESSAGE_RUN_FAILED
        assert row.error_code == "AGENT_STREAM_FAILED"


@pytest.mark.asyncio
async def test_regenerate_updates_latest_pair_in_place(agent_db, monkeypatch) -> None:
    svc = SystemAgentService()
    calls: list[dict[str, Any]] = []
    responses = iter(["原回答", "编辑后的回答", "再次生成的回答"])

    async def fake_stream(*_args, **kwargs):
        calls.append(
            {
                "user_text": kwargs["user_text"],
                "history_ids": [message.id for message in kwargs["history_messages"]],
            }
        )
        yield {
            "type": "assistant_message",
            "content": next(responses),
            "usage": {"model": "test-model"},
        }
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="原问题",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass

        original = await svc.list_messages(db, session.id)
        user_message = original[0]
        assistant_message = original[1]
        original_ids = [message.id for message in original]

        async for _event in svc.stream_message(
            db,
            session=session,
            text="编辑后的问题",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            regenerate_message=user_message,
            regenerate_assistant_message=assistant_message,
        ):
            pass

        edited = await svc.list_messages(db, session.id)
        assert [message.id for message in edited] == original_ids
        assert [message.role for message in edited] == [
            MESSAGE_ROLE_USER,
            MESSAGE_ROLE_ASSISTANT,
        ]
        assert edited[0].content["text"] == "编辑后的问题"
        assert edited[1].content["text"] == "编辑后的回答"
        assert edited[0].run_status == MESSAGE_RUN_SUCCEEDED
        assert edited[1].run_status == MESSAGE_RUN_COMPLETED
        assert calls[1] == {"user_text": "编辑后的问题", "history_ids": []}
        assert len(session.memory_state["recent_turns"]) == 1
        assert session.memory_state["recent_turns"][0] == {
            "goal": "编辑后的问题",
            "result": "编辑后的回答",
        }

        async for _event in svc.stream_message(
            db,
            session=session,
            text="",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            regenerate_message=edited[0],
            regenerate_assistant_message=edited[1],
        ):
            pass

        retried = await svc.list_messages(db, session.id)
        assert [message.id for message in retried] == original_ids
        assert retried[0].content["text"] == "编辑后的问题"
        assert retried[1].content["text"] == "再次生成的回答"
        assert calls[2]["user_text"] == "编辑后的问题"
        assert len(session.memory_state["recent_turns"]) == 1


@pytest.mark.asyncio
async def test_regenerate_history_is_not_crowded_out_by_current_tool_rows(
    agent_db,
    monkeypatch,
) -> None:
    svc = SystemAgentService()
    captured_history: list[int] = []

    async def fake_stream(*_args, **kwargs):
        captured_history.extend(message.id for message in kwargs["history_messages"])
        yield {"type": "assistant_message", "content": "新回答", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        older_user = SystemAgentMessage(
            session_id=session.id,
            role=MESSAGE_ROLE_USER,
            content={"text": "较早问题"},
            run_status=MESSAGE_RUN_SUCCEEDED,
        )
        older_assistant = SystemAgentMessage(
            session_id=session.id,
            role=MESSAGE_ROLE_ASSISTANT,
            content={"text": "较早回答"},
            run_status=MESSAGE_RUN_COMPLETED,
        )
        current_user = SystemAgentMessage(
            session_id=session.id,
            role=MESSAGE_ROLE_USER,
            content={"text": "当前问题"},
            run_status=MESSAGE_RUN_SUCCEEDED,
        )
        current_assistant = SystemAgentMessage(
            session_id=session.id,
            role=MESSAGE_ROLE_ASSISTANT,
            content={"text": "旧回答"},
            run_status=MESSAGE_RUN_COMPLETED,
        )
        db.add_all([older_user, older_assistant, current_user, current_assistant])
        await db.flush()
        db.add_all(
            [
                SystemAgentMessage(
                    session_id=session.id,
                    role=MESSAGE_ROLE_TOOL,
                    content={"tool_name": "logs.recent", "result_summary": index},
                    run_status=MESSAGE_RUN_COMPLETED,
                )
                for index in range(40)
            ]
        )
        await db.commit()

        async for _event in svc.stream_message(
            db,
            session=session,
            text="",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
            regenerate_message=current_user,
            regenerate_assistant_message=current_assistant,
        ):
            pass

        assert captured_history == [older_user.id, older_assistant.id]
        rows = await svc.list_messages(db, session.id, limit=100)
        assert [message.id for message in rows] == [
            older_user.id,
            older_assistant.id,
            current_user.id,
            current_assistant.id,
        ]


@pytest.mark.asyncio
async def test_save_config_roundtrip(agent_db) -> None:
    async with agent_db() as db:
        saved = await save_config(
            db,
            {"enabled": True, "provider_id": 3, "model": "gpt-tools", "max_steps": 6},
        )
        await db.commit()
        assert saved["enabled"] is True
        assert saved["provider_id"] == 3
        assert saved["model"] == "gpt-tools"
        assert saved["max_steps"] == 6
        row = await db.get(SystemSetting, "system_agent_config")
        assert row is not None
        assert row.value["provider_id"] == 3


@pytest.mark.asyncio
async def test_summary_compression_scheduled_with_uuid_session_id(agent_db, monkeypatch) -> None:
    """UUID 主键会话的摘要压缩必须真正被调度（旧代码 int(session.id) 抛 ValueError 被吞）。"""
    svc = SystemAgentService()
    scheduled: list[tuple[str, int]] = []

    async def fake_stream(*_a, **_k):
        yield {"type": "assistant_message", "content": "好的", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", fake_stream)

    def fake_schedule(session_id, *, summary_rev: int) -> None:
        scheduled.append((str(session_id), int(summary_rev)))

    monkeypatch.setattr(
        "app.services.system_agent.service.schedule_summary_compression", fake_schedule
    )

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        assert len(session.id) == 36 and "-" in session.id  # String(36) UUID 主键
        # 预置一段超过压缩阈值的摘要，使本轮 done 命中压缩调度分支
        # 让 recent_turns 本轮轮转出条目，从而真正刷新 memory_summary 并触发压缩调度
        session.memory_state = {
            "recent_turns": [{"goal": f"旧目标 {i}", "result": f"旧结果 {i}"} for i in range(4)],
            "summary_rev": 0,
        }
        session.memory_summary = "用户目标：整理账目。" * 200
        await db.flush()

        events = [
            event
            async for event in svc.stream_message(
                db,
                session=session,
                text="继续整理",
                role="admin",
                channel=CHANNEL_WEB,
                web_user_id=1,
            )
        ]

    assert any(e.get("type") == "done" for e in events)
    assert scheduled and scheduled[0][0] == session.id


@pytest.mark.asyncio
async def test_compress_session_summary_uses_string_primary_key(agent_db, monkeypatch) -> None:
    """后台压缩器必须按字符串 UUID 主键查会话；rev 冲突时直接短路返回。"""
    from app.services.system_agent import memory_compress

    async def failing_resolve(*_a, **_k):
        raise AssertionError("rev 冲突必须先于 provider 解析返回")

    monkeypatch.setattr(memory_compress, "resolve_agent_providers", failing_resolve)

    async with agent_db() as db:
        session = await SystemAgentService().create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        await db.commit()
        assert isinstance(session.id, str) and "-" in session.id

        captured: list[Any] = []
        real_get = db.get

        async def spy_get(model, pk, *a, **k):
            captured.append(pk)
            return await real_get(model, pk, *a, **k)

        db.get = spy_get  # type: ignore[method-assign]

        class _SessionCtx:
            async def __aenter__(self_inner):
                return db

            async def __aexit__(self_inner, *_exc) -> bool:
                return False

        monkeypatch.setattr(memory_compress, "AsyncSessionLocal", lambda: _SessionCtx())
        # expected_rev 与实际不一致：压缩器读库后应记录 rev_conflict 并返回
        await memory_compress._compress_session_summary(session.id, expected_rev=999)

        assert captured == [session.id]


# ─────────── WP-B5：密钥占位符不回泄到任何持久化面 ───────────


@pytest.mark.asyncio
async def test_secret_placeholder_roundtrip_never_leaks_into_persistence(agent_db, monkeypatch) -> None:
    """真实密钥只出现在会话的加密映射里；消息/记忆/事件/usage 全部只见占位符。"""
    svc = SystemAgentService()
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

    async def stream_turn(*_args, **kwargs):
        user_text = str(kwargs.get("user_text") or "")
        assert secret not in user_text
        assert "[[TP-CRED-1]]" in user_text
        yield {
            "type": "assistant_message",
            "content": f"已收到，我会用 [[TP-CRED-1]] 注册，密钥是 {secret}",
            "usage": {"schema_version": 2, "provider_name": "p", "model": "m"},
        }
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", stream_turn)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text=f"添加 Provider，key={secret}",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass
        await db.commit()

        # 1) 用户消息与助手消息都只见占位符
        rows = (
            await db.execute(
                text("SELECT role, content FROM system_agent_message ORDER BY id"),
            )
        ).all()
        assert rows
        for role, content in rows:
            assert secret not in str(content), f"{role} 消息泄漏了真实密钥"
        assert any("[[TP-CRED-1]]" in str(content) for _role, content in rows)

        # 2) 会话加密列必须可解密且含真实值（还原的唯一来源）
        from app.services.system_agent.secrets import decrypt_secret_map

        assert decrypt_secret_map(session.secret_map_enc) == {"[[TP-CRED-1]]": secret}

        # 3) 记忆与 usage 不出现真实密钥
        assert secret not in str(session.memory_state)
        assert secret not in str(session.memory_summary)

        # 4) 运行事件同样保持占位符
        run_rows = (
            await db.execute(text("SELECT event FROM system_agent_run_event"))
        ).all()
        for (event,) in run_rows:
            assert secret not in str(event)



@pytest.mark.asyncio
async def test_request_header_context_stays_fully_redacted_without_placeholder(agent_db, monkeypatch) -> None:
    """请求头场景维持全量打码：不产生占位符，也不写加密映射。"""
    svc = SystemAgentService()
    runtime_goals: list[str] = []

    async def stream_turn(*_args, **kwargs):
        runtime_goals.append(str(kwargs.get("user_text") or ""))
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", stream_turn)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text="request headers 里加 Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass
        await db.commit()

        assert runtime_goals
        for goal in runtime_goals:
            assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in goal
            assert "[[TP-CRED-" not in goal
            assert "[安全提示" in goal
        # 请求头场景不创建映射
        assert session.secret_map_enc is None


@pytest.mark.asyncio
async def test_placeholder_restores_only_at_owner_facing_boundaries(agent_db, monkeypatch) -> None:
    """端到端：贴 Key → 出站文本带占位符 → 消息/记忆保持占位符 → 映射可解密。"""
    from app.services.system_agent.secrets import (
        decrypt_secret_map,
        restore_placeholders,
    )

    captured: dict[str, Any] = {}
    tool_args_seen: list[dict[str, Any]] = []

    svc = SystemAgentService()
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

    async def stream_turn(*_args, **kwargs):
        captured["user_text"] = str(kwargs.get("user_text") or "")
        # secret_map 必须被传进 runtime，工具参数才能还原占位符
        captured["secret_map"] = dict(kwargs.get("secret_map") or {})
        yield {"type": "assistant_message", "content": "开始创建", "usage": {}}
        yield {"type": "tool_finished", "tool": "provider_create", "summary": {"ok": True}}
        yield {"type": "assistant_message", "content": "创建完成", "usage": {}}
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", stream_turn)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text=f"添加 Provider，base_url=https://api.example.com key={secret}",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass
        await db.commit()

        # 出站给模型的文本带占位符，真实密钥不出现
        assert secret not in captured["user_text"]
        assert "[[TP-CRED-1]]" in captured["user_text"]

        # runtime 拿到的是映射本身（供工具参数还原），run 事件仍只见占位符
        assert captured["secret_map"] == {"[[TP-CRED-1]]": secret}

        # 消息落库面只见占位符
        rows = (
            await db.execute(text("SELECT role, content FROM system_agent_message ORDER BY id"))
        ).all()
        for role, content in rows:
            assert secret not in str(content), f"{role} 消息泄漏了真实密钥"

        # 记忆与摘要同样保持占位符
        assert secret not in str(session.memory_state)

        # 映射可解密，且显示边界能还原
        secret_map = decrypt_secret_map(session.secret_map_enc)
        assert secret_map == {"[[TP-CRED-1]]": secret}
        assert restore_placeholders("我会用 [[TP-CRED-1]] 注册", secret_map) == (
            f"我会用 {secret} 注册"
        )
        assert tool_args_seen == []


@pytest.mark.asyncio
async def test_run_events_and_tool_summaries_stay_placeholder(agent_db, monkeypatch) -> None:
    """run 事件 / 工具摘要 / 不出现在显示边界的地方保持占位符。"""

    from app.services.system_agent.run_manager import _redact_event

    svc = SystemAgentService()
    secret = "sk-run-event-secret-value-1234"
    captured: dict[str, Any] = {}

    async def stream_turn(*_args, **kwargs):
        captured["secret_map"] = dict(kwargs.get("secret_map") or {})
        # 模型回复里只有占位符；真实值只在 ToolContext.secret_map 里
        yield {
            "type": "assistant_message",
            "content": "好的，我会用 [[TP-CRED-1]] 注册。",
            "usage": {},
        }
        yield {
            "type": "tool_finished",
            "tool": "providers.probe_and_add",
            "call_id": "call-1",
            "tool_name": "providers.probe_and_add",
            "is_error": False,
            "result_summary": {"echo": "收到 [[TP-CRED-1]]"},
        }
        yield {"type": "done", "ok": True}

    monkeypatch.setattr(svc.runtime, "stream_turn", stream_turn)

    async with agent_db() as db:
        session = await svc.create_session(db, channel=CHANNEL_WEB, web_user_id=1)
        async for _event in svc.stream_message(
            db,
            session=session,
            text=f"添加 Provider，base_url=https://api.example.com key={secret}",
            role="admin",
            channel=CHANNEL_WEB,
            web_user_id=1,
        ):
            pass
        await db.commit()

        # 工具摘要落库面只见占位符，不出现真实值
        rows = (
            await db.execute(
                text(
                    "SELECT role, content FROM system_agent_message "
                    "WHERE role = 'tool' ORDER BY id"
                )
            )
        ).all()
        assert rows, "工具摘要应已落库"
        for _role, content in rows:
            assert secret not in str(content)

    # run 事件库脱敏后也不出现真实值：`_redact_event` 面对的是占位符文本，
    # 还原不应该发生在这个面上
    event = _redact_event(
        {"type": "assistant_message", "content": "我会用 [[TP-CRED-1]] 注册"},
        (secret,),
    )
    assert secret not in str(event)
    assert "[[TP-CRED-1]]" in str(event)
    assert captured["secret_map"] == {"[[TP-CRED-1]]": secret}
