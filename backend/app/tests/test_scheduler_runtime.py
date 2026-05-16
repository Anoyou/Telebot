from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.llm_client import LLMResult
from app.services.llm_dto import LLMProviderDTO
from app.worker.command import CommandContext, set_command_context
from app.worker.scheduler_runtime import PlatformScheduler, SchedulerRuleExecutor


async def _noop_log(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
    return None


def _runtime() -> PlatformScheduler:
    paused = asyncio.Event()
    paused.set()
    return PlatformScheduler(
        account_id=42,
        client=AsyncMock(),
        redis=AsyncMock(),
        paused=paused,
        log_writer=_noop_log,
    )


@pytest.mark.asyncio
async def test_plugin_facade_registers_interval_job(monkeypatch) -> None:
    monkeypatch.setattr("app.worker.scheduler_runtime._get_system_tz", AsyncMock(return_value=None))

    runtime = _runtime()
    callback = AsyncMock()

    facade = runtime.for_plugin("demo", generation=1)
    facade.register("heartbeat", {"kind": "interval", "interval_sec": 60}, callback)

    await runtime.tick_runtime_jobs()

    callback.assert_awaited_once()
    job = callback.await_args.args[0]
    assert job.account_id == 42
    assert job.owner == "demo"
    assert job.job_id == "heartbeat"
    assert job.fire_count == 1

    jobs = facade.list_jobs()
    assert jobs[0]["fire_count"] == 1
    assert jobs[0]["config"]["last_result"] == "ok"
    assert jobs[0]["config"]["next_fire"] is not None


@pytest.mark.asyncio
async def test_unregister_owner_prevents_runtime_job(monkeypatch) -> None:
    monkeypatch.setattr("app.worker.scheduler_runtime._get_system_tz", AsyncMock(return_value=None))

    runtime = _runtime()
    callback = AsyncMock()
    facade = runtime.for_plugin("demo", generation=1)
    facade.register(
        "once",
        {
            "kind": "once",
            "fire_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
        callback,
    )

    removed = facade.unregister_all()
    await runtime.tick_runtime_jobs()

    assert removed == 1
    callback.assert_not_awaited()
    assert facade.list_jobs() == []


@pytest.mark.asyncio
async def test_runtime_job_failure_is_logged_and_kept(monkeypatch) -> None:
    monkeypatch.setattr("app.worker.scheduler_runtime._get_system_tz", AsyncMock(return_value=None))
    logs: list[tuple[tuple, dict]] = []

    async def _log(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        logs.append((args, kwargs))

    paused = asyncio.Event()
    paused.set()
    runtime = PlatformScheduler(
        account_id=42,
        client=AsyncMock(),
        redis=AsyncMock(),
        paused=paused,
        log_writer=_log,
    )

    async def _boom(_job) -> None:  # noqa: ANN001
        raise RuntimeError("boom")

    runtime.for_plugin("demo", generation=1).register(
        "broken",
        {"kind": "interval", "interval_sec": 60},
        _boom,
    )

    await runtime.tick_runtime_jobs()

    jobs = runtime.list_runtime_jobs()
    assert jobs[0]["fire_count"] == 0
    assert jobs[0]["last_error"] == "RuntimeError: boom"
    assert jobs[0]["config"]["last_result"] == "error"
    assert logs
    assert logs[0][1]["source"] == "plugin"
    assert logs[0][1]["plugin_key"] == "demo"


@pytest.mark.asyncio
async def test_action_call_llm_uses_shared_service_invoke(monkeypatch) -> None:
    executor = SchedulerRuleExecutor()
    row = SimpleNamespace(
        id=7,
        name="primary",
        provider="openai",
        api_key_enc=None,
        base_url=None,
        default_model="gpt-4o",
        api_format=None,
        proxy_url=None,
        modality="text",
        tags=[],
        cost_tier=2,
    )
    fallback = SimpleNamespace(
        id=8,
        name="fallback",
        provider="openai",
        api_key_enc=None,
        base_url=None,
        default_model="gpt-4o-mini",
        api_format=None,
        proxy_url=None,
        modality="text",
        tags=[],
        cost_tier=2,
    )
    monkeypatch.setattr(executor, "get_provider_row", AsyncMock(return_value=row))
    monkeypatch.setattr(executor, "get_provider_rows", AsyncMock(return_value=[row, fallback]))
    send_mock = AsyncMock(return_value=object())
    monkeypatch.setattr(executor, "send_with_ratelimit", send_mock)

    result = LLMResult(text="done", model="gpt-4o", input_tokens=2, output_tokens=3)
    invoke_mock = AsyncMock(
        return_value=(
            result,
            LLMProviderDTO(id=7, name="primary", provider="openai", default_model="gpt-4o"),
            False,
        )
    )
    monkeypatch.setattr("app.worker.scheduler_runtime.invoke_ai_runtime", invoke_mock)

    ctx = SimpleNamespace(account_id=42, log=AsyncMock())
    action = {
        "provider_id": 7,
        "prompt": "hello",
        "target_chat_id": 123,
        "fallback_provider_id": 8,
        "system_prompt": "sys",
        "max_tokens": 32,
    }

    await executor.action_call_llm(ctx, action)

    invoke_mock.assert_awaited_once()
    provider_dto, provider_map, system, user = invoke_mock.await_args.args[:4]
    assert provider_dto.id == 7
    assert provider_map[8].name == "fallback"
    assert system == "sys"
    assert user == "hello"
    send_mock.assert_awaited_once_with(ctx, 123, "done")


@pytest.mark.asyncio
async def test_scheduler_send出口_blocks_non_whitelisted_command_text() -> None:
    set_command_context(
        CommandContext(
            account_id=42,
            templates={},
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["允许"],
        )
    )
    executor = SchedulerRuleExecutor()
    ctx = SimpleNamespace(account_id=42, engine=AsyncMock(), client=AsyncMock(), log=AsyncMock())
    cfg = {
        "action": {
            "type": "send_message",
            "target_chat_id": 123,
            "text": "。禁止",
        }
    }

    ok = await executor.fire(ctx, 9, cfg)

    assert ok is False
    assert "blocked by whitelist" in cfg["last_error"]
    ctx.client.send_message.assert_not_awaited()
