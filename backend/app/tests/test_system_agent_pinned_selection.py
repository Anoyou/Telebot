"""固定模型必须贯穿真实能力校验、分类器和 Agent 调用，不只验证初始选择。"""

from __future__ import annotations

import socket
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models.command import LLMProvider, LLMProviderKey
from app.db.models.system import SystemSetting
from app.db.models.system_agent import SystemAgentSession
from app.services import llm_account_budget, llm_runtime, llm_usage_service
from app.services.llm_client import LLMError, LLMErrorScope
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_protocol import ModelResponse, ModelStreamEvent, ModelUsage, TextContent
from app.services.system_agent import memory_compress, model_capability
from app.services.system_agent import runtime as runtime_module
from app.services.system_agent.config import CONFIG_KEY, ResolvedAgentProviders, tools_models_for_dto
from app.services.system_agent.registry import ToolRegistry, ToolSpec
from app.tests._sqlite_engine import create_file_sqlite_engine


@pytest.fixture
async def selection_env(tmp_path, monkeypatch):
    def no_network(*_args, **_kwargs):
        pytest.fail("pinned selection tests must not access the network")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(model_capability, "build_client_from_dto", no_network)

    engine = create_file_sqlite_engine(tmp_path / "selection.sqlite")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE proxy (id INTEGER PRIMARY KEY)"))
        for table in (LLMProvider.__table__, LLMProviderKey.__table__, SystemSetting.__table__):
            await conn.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        primary = LLMProvider(
            id=1,
            name="selected-provider",
            provider="openai",
            api_format="responses",
            base_url="https://example.invalid/v1",
            default_model="model-b",
            api_key_enc="test-only-encrypted",
            models=[
                {"id": "model-b", "enabled": True, "supports_tools": True},
                {
                    "id": "model-a", "enabled": True, "supports_tools": True,
                    "supports_images": False, "max_output_tokens": 4096,
                    "supported_api_formats": ["responses"], "label": "用户选择",
                },
                {"id": "disabled-model", "enabled": False, "supports_tools": True},
                {"id": "no-tools-model", "enabled": True, "supports_tools": False},
            ],
            pool_keys=[],
        )
        other = LLMProvider(
            id=2,
            name="other-provider",
            provider="openai",
            api_format="responses",
            base_url="https://example.invalid/v1",
            default_model="model-c",
            api_key_enc="test-only-encrypted",
            models=[{"id": "model-c", "enabled": True, "supports_tools": True}],
            pool_keys=[],
        )
        db.add_all([
            primary, other,
            SystemSetting(key=CONFIG_KEY, value={
                "enabled": True, "provider_id": 1, "model": "model-a", "fallback_provider_ids": [2],
            }),
        ])
        await db.commit()
        env = SimpleNamespace(
            factory=factory,
            primary=LLMProviderDTO.from_orm_row(primary),
            other=LLMProviderDTO.from_orm_row(other),
            calls=[],
            failures=set(),
            probe_status="supported",
            response_model=None,
        )

    async def probe(_provider, _model):
        return model_capability.CapabilityProbeResult(env.probe_status, datetime.now(UTC))

    env.probe = AsyncMock(side_effect=probe)
    env.refresh = Mock()
    monkeypatch.setattr(model_capability, "probe_model_tool_capability", env.probe)
    monkeypatch.setattr(model_capability, "schedule_capability_refresh", env.refresh)
    monkeypatch.setattr(llm_usage_service, "ensure_llm_usage_callback_registered", Mock())
    monkeypatch.setattr(llm_runtime, "_check_budget", AsyncMock(return_value=llm_runtime.BudgetCheck()))
    monkeypatch.setattr(llm_runtime, "_emit_usage", AsyncMock())
    monkeypatch.setattr(llm_account_budget, "settle", AsyncMock())
    monkeypatch.setattr(llm_runtime, "_open_key_selection", AsyncMock(return_value=SimpleNamespace(
        apply=lambda provider: provider,
        usage_fields=lambda: {},
        report_success=AsyncMock(),
        report_failure=AsyncMock(return_value=False),
    )))

    def client(provider, **_kwargs):
        def reply(request):
            stage = "router" if request.max_output_tokens == 160 else "answer"
            env.calls.append((stage, provider.id, request))
            if (stage, request.model) in env.failures:
                raise LLMError("mock model unavailable", scope=LLMErrorScope.PROVIDER_LOCAL)
            content = (
                '{"needs_tools":true,"domains":["scheduler"],"reason":"lookup"}'
                if stage == "router" else "已完成"
            )
            return ModelResponse(
                model=env.response_model or request.model,
                content=(TextContent(content),),
                usage=ModelUsage(input_tokens=2, output_tokens=1),
            )

        class Client:
            async def invoke(self, request):
                return reply(request)

            async def stream_invoke(self, request):
                yield ModelStreamEvent(response=reply(request))

        return Client()

    monkeypatch.setattr(llm_runtime, "build_client_from_dto", client)
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="scheduler.list", description="列出定时任务。",
        input_schema={"type": "object", "properties": {}},
        read_handler=AsyncMock(return_value={"items": []}),
    ))
    env.runtime = runtime_module.SystemAgentRuntime(registry, tool_session_factory=factory)
    try:
        yield env
    finally:
        await engine.dispose()


async def _pin(env, *, model="model-a"):
    previous = ResolvedAgentProviders(
        primary=env.other, model="model-c", providers={1: env.primary, 2: env.other},
    )
    async with env.factory() as db:
        return await runtime_module._apply_pinned_selection(
            db, previous, {"mode": "pinned", "provider_id": 1, "model": model},
        )


async def _cache(env, *, a="supported", b="supported", stale=False):
    now = datetime.now(UTC) - (timedelta(hours=1) if stale else timedelta())
    entries = {
        model_capability._provider_signature(provider, model): {
            "status": status, "checked_at": now.isoformat(), "error_type": None,
        }
        for provider, model, status in (
            (env.primary, "model-a", a), (env.primary, "model-b", b), (env.other, "model-c", "supported"),
        )
        if status is not None
    }
    async with env.factory() as db:
        db.add(SystemSetting(key=model_capability.CACHE_KEY, value={"version": 1, "entries": entries}))
        await db.commit()


async def _turn(env, *, mode="pinned", user_text="帮我查一下相关配置"):
    async with env.factory() as db:
        return [event async for event in env.runtime.stream_turn(
            db,
            session=SimpleNamespace(
                id="pinned-test-session", account_id=None, web_user_id=None,
                memory_summary="", memory_state={},
            ),
            user_text=user_text, role="admin", channel="web",
            model_selection={"mode": mode, "provider_id": 1, "model": "model-a"},
            fallback_provider_id=2,
        )]


def _called_models(env, stage):
    return [(pid, request.model) for kind, pid, request in env.calls if kind == stage]


async def test_pinned_selection_limits_request_dto_without_mutating_config(selection_env):
    env = selection_env
    original = deepcopy(env.primary.models)
    resolved = await _pin(env)
    assert isinstance(resolved, ResolvedAgentProviders)
    assert resolved.model == resolved.primary.default_model == "model-a"
    assert resolved.primary.models == [original[1]]
    assert resolved.providers == {1: resolved.primary}
    assert tools_models_for_dto(resolved.primary, resolved.model) == ["model-a"]
    assert resolved.primary.capabilities_for_model("model-a").images is False
    assert env.primary.models == original
    async with env.factory() as db:
        stored = await db.get(LLMProvider, 1)
        assert stored.models == original
        assert stored.default_model == "model-b"


async def test_pinned_legacy_provider_without_model_list_only_probes_selected(selection_env):
    env = selection_env
    async with env.factory() as db:
        row = await db.get(LLMProvider, 1)
        row.models = []
        await db.commit()
    resolved = await _pin(env)
    async with env.factory() as db:
        verified = await model_capability.verify_resolved_agent_providers(db, resolved, non_blocking=True)
    assert isinstance(verified, ResolvedAgentProviders)
    assert verified.model == "model-a"
    assert verified.primary.enabled_model_ids() == ["model-a"]
    assert [(p.id, m) for p, m, _ in env.refresh.call_args.args[0]] == [(1, "model-a")]


@pytest.mark.parametrize("model", ["disabled-model", "no-tools-model", "missing-model"])
async def test_pinned_selection_does_not_enable_invalid_models(selection_env, model):
    result = await _pin(selection_env, model=model)
    assert isinstance(result, str)
    assert model in result
    selection_env.probe.assert_not_awaited()
    selection_env.refresh.assert_not_called()


@pytest.mark.parametrize("probe_status", ["unsupported", "unavailable"])
@pytest.mark.parametrize("sibling_status", ["supported", None], ids=["verified-sibling", "cold-sibling"])
@pytest.mark.parametrize("stale", [False, True], ids=["fresh-cache", "stale-cache"])
async def test_real_verifier_cannot_replace_pinned_unsupported_model(
    selection_env, probe_status, sibling_status, stale,
):
    env = selection_env
    await _cache(env, a="unsupported", b=sibling_status, stale=stale)
    env.probe_status = probe_status
    resolved = await _pin(env)
    async with env.factory() as db:
        result = await model_capability.verify_resolved_agent_providers(db, resolved, non_blocking=True)
    assert isinstance(result, str), "能力校验不得把用户选择的 A 改为同 Provider 的 B"
    assert [(call.args[0].id, call.args[1]) for call in env.probe.await_args_list] == [(1, "model-a")]
    env.refresh.assert_not_called()


@pytest.mark.parametrize("status", ["unsupported", "unavailable"])
async def test_blocking_verifier_only_probes_pinned_model(selection_env, status):
    env = selection_env
    env.probe_status = status
    resolved = await _pin(env)
    async with env.factory() as db:
        result = await model_capability.verify_resolved_agent_providers(db, resolved)
    assert isinstance(result, str)
    assert [(call.args[0].id, call.args[1]) for call in env.probe.await_args_list] == [(1, "model-a")]
    env.refresh.assert_not_called()


async def test_auto_cold_cache_retains_all_candidate_refreshes(selection_env):
    env = selection_env
    events = await _turn(env, mode="auto")
    assert events[-1]["ok"] is True
    env.probe.assert_not_awaited()
    assert [(p.id, m) for p, m, _ in env.refresh.call_args.args[0]] == [
        (1, "model-a"), (1, "model-b"), (2, "model-c"),
    ]


@pytest.mark.parametrize("cached_status", [None, "supported", "unavailable"])
async def test_pinned_turn_keeps_selection_and_only_refreshes_selected_model(selection_env, cached_status):
    env = selection_env
    await _cache(env, a=cached_status, b=None)
    env.response_model = "upstream-a-revision"
    events = await _turn(env)
    assert events[-1]["ok"] is True
    assert _called_models(env, "router") == [(1, "model-a")]
    assert _called_models(env, "answer") == [(1, "model-a")]
    for _kind, _pid, request in env.calls:
        assert request.metadata["model_pinned"] is True
        assert request.metadata["allowed_cross_provider_ids"] == []
    env.probe.assert_not_awaited()
    if cached_status == "supported":
        env.refresh.assert_not_called()
    else:
        assert [(p.id, m) for p, m, _ in env.refresh.call_args.args[0]] == [(1, "model-a")]
    selected = next(event for event in events if event["type"] == "provider_selected")
    assert selected["model"] == "model-a"
    usage = next(event["usage"] for event in events if event["type"] == "assistant_message")
    assert usage["requested_provider_id"] == usage["provider_id"] == 1
    assert usage["requested_model"] == "model-a"
    assert usage["model"] == "upstream-a-revision"
    assert usage["selection_mode"] == "pinned"


@pytest.mark.parametrize("mode", ["pinned", "auto"])
@pytest.mark.parametrize("probe_status", ["unsupported", "unavailable"])
async def test_runtime_real_verifier_rejects_pin_but_auto_can_select_sibling(selection_env, mode, probe_status):
    env = selection_env
    await _cache(env, a="unsupported")
    env.probe_status = probe_status
    events = await _turn(env, mode=mode, user_text="列出定时任务")
    env.probe.assert_awaited_once()
    assert env.probe.await_args.args[1] == "model-a"
    if mode == "pinned":
        assert events[-1]["ok"] is False
        assert _called_models(env, "answer") == []
        assert not any(event["type"] == "provider_selected" for event in events)
        error = next(event for event in events if event["type"] == "error")
        assert error["code"] == "MODEL_TOOLS_UNAVAILABLE"
        assert "model-a" in error["message"]
        assert "选择" in error["hint"]
    else:
        assert events[-1]["ok"] is True
        assert _called_models(env, "answer") == [(1, "model-b")]
        usage = next(event["usage"] for event in events if event["type"] == "assistant_message")
        assert usage["requested_model"] == "model-a"
        assert usage["model"] == "model-b"
        assert usage["selection_mode"] == "auto"


@pytest.mark.parametrize("mode", ["pinned", "auto"])
@pytest.mark.parametrize("stream", [False, True], ids=["invoke", "stream"])
async def test_main_model_fallback_respects_selection_mode(selection_env, monkeypatch, mode, stream):
    env = selection_env
    await _cache(env)
    env.failures.add(("answer", "model-a"))
    if not stream:
        real_run_agent = runtime_module.run_agent

        async def non_stream_agent(*args, **kwargs):
            kwargs["stream_model_call"] = None
            return await real_run_agent(*args, **kwargs)

        monkeypatch.setattr(runtime_module, "run_agent", non_stream_agent)
    events = await _turn(env, mode=mode, user_text="列出定时任务")
    if mode == "pinned":
        assert events[-1]["ok"] is False
        assert _called_models(env, "answer") == [(1, "model-a")]
        assert any(event["type"] == "error" for event in events)
    else:
        assert events[-1]["ok"] is True
        assert _called_models(env, "answer") == [(1, "model-a"), (1, "model-b")]
        usage = next(event["usage"] for event in events if event["type"] == "assistant_message")
        assert usage["requested_model"] == "model-a"
        assert usage["model"] == "model-b"


@pytest.mark.parametrize("mode", ["pinned", "auto"])
async def test_intent_classifier_fallback_respects_selection_mode(selection_env, mode):
    env = selection_env
    await _cache(env)
    env.failures.add(("router", "model-a"))
    events = await _turn(env, mode=mode)
    assert events[-1]["ok"] is True
    assert _called_models(env, "answer") == [(1, "model-a")]
    expected = [(1, "model-a")] if mode == "pinned" else [(1, "model-a"), (1, "model-b")]
    assert _called_models(env, "router") == expected
    router_requests = [request for kind, _pid, request in env.calls if kind == "router"]
    assert all(request.metadata["model_pinned"] == (mode == "pinned") for request in router_requests)
    route = next(event for event in events if event["type"] == "route_selected")
    assert route["route_source"] == ("fallback" if mode == "pinned" else "model")


async def test_summary_compression_still_uses_global_provider_after_pinned_turn(selection_env, monkeypatch):
    env = selection_env
    await _cache(env)
    async with env.factory() as db:
        config = await db.get(SystemSetting, CONFIG_KEY)
        config.value = {"enabled": True, "provider_id": 2, "model": "model-c"}
        await db.commit()
    events = await _turn(env)
    assert events[-1]["ok"] is True
    assert _called_models(env, "answer") == [(1, "model-a")]

    async with env.factory() as db:
        async with db.bind.begin() as conn:
            await conn.execute(text("CREATE TABLE web_user (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY)"))
            await conn.run_sync(SystemAgentSession.__table__.create)
        db.add(SystemAgentSession(
            id="pinned-test-session", channel="web", memory_summary="历史对话摘要" * 400,
            memory_state={"summary_rev": 1},
        ))
        await db.commit()
    monkeypatch.setattr(memory_compress, "AsyncSessionLocal", env.factory)
    env.calls.clear()
    await memory_compress._compress_session_summary("pinned-test-session", expected_rev=1)
    assert _called_models(env, "answer") == [(2, "model-c")]
    assert env.calls[0][2].metadata == {}
    assert llm_runtime._emit_usage.await_args.args[0].source == "system_agent_memory"
    async with env.factory() as db:
        session = await db.get(SystemAgentSession, "pinned-test-session")
        assert session.memory_summary == "已完成"
