from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.system_agent import SystemAgentMessage, SystemAgentSession
from app.services.llm_agent import AgentResult
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_protocol import ImageContent, ModelUsage, StopReason
from app.services.system_agent import prompts
from app.services.system_agent import runtime as runtime_module
from app.services.system_agent.config import ResolvedAgentProviders
from app.services.system_agent.memory import memory_context, update_session_memory
from app.services.system_agent.registry import ToolRegistry
from app.services.system_agent.runtime import SystemAgentRuntime
from app.services.system_agent.service import SystemAgentService


def _message(index: int, *, size: int = 50) -> SystemAgentMessage:
    return SystemAgentMessage(
        id=index,
        session_id="cache-session",
        role="user" if index % 2 else "assistant",
        content={"text": str(index).ljust(size, "x")},
        run_status="succeeded" if index % 2 else "completed",
    )


@pytest.mark.asyncio
async def test_turn_context_keeps_system_and_completed_history_prefix_stable(monkeypatch):
    provider = LLMProviderDTO(id=1, name="test", provider="openai", default_model="test")

    async def flags(_db):
        return {
            "timezone": "Asia/Shanghai", "command_prefix": "/", "ai_enabled": True,
            "agent_config": {"enabled": True, "session_token_limit": 16384},
        }

    async def resolve(_db, _cfg):
        return ResolvedAgentProviders(primary=provider, model="test", providers={1: provider})

    async def verify(_db, resolved, **_kwargs):
        return resolved

    async def long_term(*_args, **_kwargs):
        return "长期偏好：中文"

    class Skills:
        def select(self, _route):
            return [SimpleNamespace(name="test-skill")]

        def narrow_tools(self, specs, _skills):
            return specs

        def understanding_summary(self, _skills):
            return "测试技能"

        def render_prompt(self, _skills):
            return "技能内容：测试技能"

    captured = []

    async def run(_call, request, _tools, **_kwargs):
        captured.append(request)
        return AgentResult(
            text="已回答", model="test", messages=request.messages,
            usage=ModelUsage(), steps=1, tool_calls=0, stop_reason=StopReason.COMPLETED,
        )

    monkeypatch.setattr(runtime_module, "load_system_context_flags", flags)
    monkeypatch.setattr(runtime_module, "resolve_agent_providers", resolve)
    monkeypatch.setattr(runtime_module, "verify_resolved_agent_providers", verify)
    monkeypatch.setattr(runtime_module, "run_agent", run)
    monkeypatch.setattr("app.services.system_agent.user_memory.prompt_block_for_scope", long_term)
    session = SystemAgentSession(id="cache-session", account_id=None, web_user_id=1,
                                 memory_summary="摘要一", memory_state={})
    runtime = SystemAgentRuntime(ToolRegistry(), skill_registry=Skills())
    first_user = _message(1)
    first_user.content = {
        "text": "第一轮",
        "attachments": [{"type": "image", "source": "remote_url", "url": "https://example.com/image.png"}],
    }
    for day, user, history in [(16, first_user, []), (17, _message(3), [first_user, _message(2)])]:
        class Clock:
            @staticmethod
            def now(_tz, day=day):
                return datetime(2026, 10, day, 12, 30, tzinfo=UTC)

        monkeypatch.setattr(prompts, "datetime", Clock)
        events = [event async for event in runtime.stream_turn(
            AsyncMock(), session=session, user_text=user.content["text"],
            user_images=(ImageContent(url="https://example.com/image.png"),),
            role="admin", channel="web", history_messages=history, context_user_message=user,
        )]
        assert events[-1]["type"] == "done"
        session.memory_summary = "摘要二"

    assert captured[0].messages[0] == captured[1].messages[0]
    assert captured[0].messages == captured[1].messages[:2]
    first_tail = captured[0].messages[-1].text_content()
    for marker in ["2026-10-16", "长期偏好：中文", "摘要一", "技能内容", "本轮工具状态", "原生媒体输入"]:
        assert marker in first_tail
        assert marker not in captured[0].messages[0].text_content()
    assert "2026-10-17" in captured[1].messages[-1].text_content()
    assert "摘要二" in captured[1].messages[-1].text_content()
    assert captured[1].messages[1].text_content() == first_tail


def test_image_only_turn_replays_saved_context():
    message = _message(1)
    message.content = {"text": "", "model_turn_context": "图片说明"}
    assert runtime_module._message_text(message) == runtime_module._with_turn_context("", "图片说明")


def test_usage_event_preserves_cache_metrics():
    provider = LLMProviderDTO(id=1, name="test", provider="openai", default_model="test")
    payload = runtime_module._usage_payload(
        ModelUsage(input_tokens=20, output_tokens=5, cache_read_tokens=60, cache_write_tokens=10),
        provider,
        "test",
    )
    assert payload["cache_read_tokens"] == 60
    assert payload["cache_write_tokens"] == 10


def test_history_within_budget_is_append_only_and_longer_than_eight():
    runtime = SystemAgentRuntime(ToolRegistry())
    history = [_message(index) for index in range(1, 41)]
    first = runtime._build_messages(system_prompt="stable", history=history,
                                    user_text="now", token_budget=16384)
    second = runtime._build_messages(system_prompt="stable", history=history + [_message(41), _message(42)],
                                     user_text="next", token_budget=16384)
    assert len(first) == 42
    assert first[:-1] == second[:len(first) - 1]


def test_budget_overflow_drops_a_block_and_keeps_persisted_start():
    runtime = SystemAgentRuntime(ToolRegistry())
    session = SystemAgentSession(id="cache-session", memory_summary="", memory_state={})
    history = [_message(index, size=100) for index in range(1, 41)]
    first = runtime._build_messages(system_prompt="stable", history=history,
                                    user_text="now", token_budget=1100, session=session)
    assert session.memory_state["context_start_message_id"] == 11
    assert len(first[1:-1]) == 30
    update_session_memory(session, user_text="now", assistant_text="ok", domains=[], tool_events=[])
    assert session.memory_state["context_start_message_id"] == 11
    assert "context_start_message_id" not in memory_context(session)
    second = runtime._build_messages(system_prompt="stable", history=history + [_message(41), _message(42)],
                                     user_text="next", token_budget=1100, session=session)
    assert first[1] == second[1]
    assert len(second[1:-1]) == 32


@pytest.mark.asyncio
async def test_service_history_has_no_count_limit_and_respects_window_start():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE web_user (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY)"))
            await conn.run_sync(SystemAgentSession.__table__.create)
            await conn.run_sync(SystemAgentMessage.__table__.create)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            session = SystemAgentSession(id="cache-session", channel="web", memory_state={})
            db.add(session)
            db.add_all([_message(index) for index in range(1, 541)])
            await db.flush()
            service = SystemAgentService()
            assert len(await service._model_history(db, session, before_id=541)) == 540
            session.memory_state = {"context_start_message_id": 11}
            rows = await service._model_history(db, session, before_id=41)
            assert [row.id for row in rows] == list(range(11, 41))
    finally:
        await engine.dispose()
