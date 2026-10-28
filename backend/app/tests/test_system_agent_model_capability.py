from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models.system import SystemSetting
from app.services.llm_client import LLMError, LLMErrorScope
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_protocol import ModelResponse, ToolCall
from app.services.system_agent import model_capability
from app.services.system_agent.config import ResolvedAgentProviders
from app.tests._sqlite_engine import create_file_sqlite_engine


@pytest.fixture
async def setting_db(tmp_path):
    engine = create_file_sqlite_engine(tmp_path / "settings.sqlite")
    async with engine.begin() as conn:
        await conn.run_sync(SystemSetting.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _provider(provider_id: int, *, models: list[str]) -> LLMProviderDTO:
    return LLMProviderDTO(
        id=provider_id,
        name=f"provider-{provider_id}",
        provider="openai",
        api_format="responses",
        base_url="https://example.invalid/v1",
        default_model=models[0],
        api_key_enc="encrypted",
        models=[
            {"id": model, "enabled": True, "supports_tools": True}
            for model in models
        ],
    )


@pytest.mark.asyncio
async def test_probe_requires_expected_tool_call(monkeypatch) -> None:
    captured = None

    class Client:
        async def invoke(self, request):  # noqa: ANN001
            nonlocal captured
            captured = request
            return ModelResponse(
                model=request.model,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name=model_capability.PROBE_TOOL_NAME,
                        arguments={"nonce": model_capability.PROBE_NONCE},
                    ),
                ),
            )

    monkeypatch.setattr(model_capability, "build_client_from_dto", lambda *_a, **_k: Client())
    result = await model_capability.probe_model_tool_capability(_provider(1, models=["m1"]), "m1")

    assert result.supported is True
    assert captured.tool_choice.name == model_capability.PROBE_TOOL_NAME
    assert len(captured.tools) == 1
    assert captured.tools[0].strict is False
    assert captured.max_output_tokens >= 256
    assert captured.metadata.get("thinking") is None


@pytest.mark.asyncio
async def test_probe_disables_thinking_for_deepseek_providers(monkeypatch) -> None:
    """DeepSeek V4 默认 thinking=enabled，探测应显式关闭以便强制 tool_choice。"""

    captured = None

    class Client:
        async def invoke(self, request):  # noqa: ANN001
            nonlocal captured
            captured = request
            return ModelResponse(
                model=request.model,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name=model_capability.PROBE_TOOL_NAME,
                        arguments={"nonce": model_capability.PROBE_NONCE},
                    ),
                ),
            )

    deepseek = LLMProviderDTO(
        id=9,
        name="deepseek-O",
        provider="openai",
        api_format="chat_completions",
        base_url="https://api.deepseek.com",
        default_model="deepseek-v4-pro",
        api_key_enc="encrypted",
        models=[{"id": "deepseek-v4-pro", "enabled": True, "supports_tools": True}],
    )
    monkeypatch.setattr(model_capability, "build_client_from_dto", lambda *_a, **_k: Client())
    result = await model_capability.probe_model_tool_capability(deepseek, "deepseek-v4-pro")

    assert result.supported is True
    assert captured.metadata.get("thinking") == "disabled"


@pytest.mark.asyncio
async def test_probe_accepts_correct_tool_even_if_nonce_wrong(monkeypatch) -> None:
    """DeepSeek 等兼容站强制 tool_choice 后可能改写参数，仍应视为支持 tools。"""

    class Client:
        async def invoke(self, request):  # noqa: ANN001
            return ModelResponse(
                model=request.model,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name=model_capability.PROBE_TOOL_NAME,
                        arguments={"nonce": "not-the-expected-value"},
                    ),
                ),
            )

    monkeypatch.setattr(model_capability, "build_client_from_dto", lambda *_a, **_k: Client())
    result = await model_capability.probe_model_tool_capability(_provider(1, models=["m1"]), "m1")
    assert result.supported is True


@pytest.mark.asyncio
async def test_probe_rejects_plain_text_success(monkeypatch) -> None:
    class Client:
        async def invoke(self, request):  # noqa: ANN001
            return ModelResponse(model=request.model)

    monkeypatch.setattr(model_capability, "build_client_from_dto", lambda *_a, **_k: Client())
    result = await model_capability.probe_model_tool_capability(_provider(1, models=["m1"]), "m1")
    assert result.status == "unsupported"


@pytest.mark.asyncio
async def test_probe_falls_back_to_auto_when_thinking_rejects_forced_tool_choice(
    monkeypatch,
) -> None:
    """DeepSeek 思考模式拒绝强制 tool_choice 时，应降级 auto 并仍判 supported。"""

    from app.services.llm_protocol import NamedToolChoice, ToolChoiceMode

    choices: list[object] = []

    class Client:
        async def invoke(self, request):  # noqa: ANN001
            choices.append(request.tool_choice)
            if isinstance(request.tool_choice, NamedToolChoice):
                raise LLMError(
                    'Anthropic 接口返回 400: {"error":{"message":'
                    '"Thinking mode does not support this tool_choice"}}',
                    scope=LLMErrorScope.CAPABILITY_MISMATCH,
                    status_code=400,
                )
            return ModelResponse(
                model=request.model,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name=model_capability.PROBE_TOOL_NAME,
                        arguments={"nonce": model_capability.PROBE_NONCE},
                    ),
                ),
            )

    monkeypatch.setattr(model_capability, "build_client_from_dto", lambda *_a, **_k: Client())
    result = await model_capability.probe_model_tool_capability(_provider(1, models=["m1"]), "m1")

    assert result.supported is True
    assert isinstance(choices[0], NamedToolChoice)
    assert choices[1] is ToolChoiceMode.AUTO


@pytest.mark.asyncio
async def test_probe_marks_thinking_tool_choice_error_unavailable_without_success(
    monkeypatch,
) -> None:
    """强制与 auto 都因思考模式 tool_choice 失败时，标 unavailable 而非永久 unsupported。"""

    class Client:
        async def invoke(self, _request):  # noqa: ANN001
            raise LLMError(
                "Thinking mode does not support this tool_choice",
                scope=LLMErrorScope.CAPABILITY_MISMATCH,
                status_code=400,
            )

    monkeypatch.setattr(model_capability, "build_client_from_dto", lambda *_a, **_k: Client())
    result = await model_capability.probe_model_tool_capability(_provider(1, models=["m1"]), "m1")
    assert result.status == "unavailable"
    assert result.error_type == "LLMError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMError("503", retryable=True, status_code=503), "unavailable"),
        (
            LLMError("tools unsupported", scope=LLMErrorScope.CAPABILITY_MISMATCH),
            "unsupported",
        ),
    ],
)
async def test_probe_distinguishes_transient_and_capability_errors(
    monkeypatch,
    error: Exception,
    expected: str,
) -> None:
    class Client:
        async def invoke(self, _request):  # noqa: ANN001
            raise error

    monkeypatch.setattr(model_capability, "build_client_from_dto", lambda *_a, **_k: Client())
    result = await model_capability.probe_model_tool_capability(_provider(1, models=["m1"]), "m1")
    assert result.status == expected


@pytest.mark.asyncio
async def test_verify_filters_models_and_reuses_persistent_cache(setting_db, monkeypatch) -> None:
    primary = _provider(1, models=["good", "bad"])
    fallback = _provider(2, models=["fallback"])
    resolved = ResolvedAgentProviders(
        primary=primary,
        model="good",
        providers={1: primary, 2: fallback},
    )
    calls: list[tuple[int, str]] = []

    async def probe(provider, model):  # noqa: ANN001
        calls.append((provider.id, model))
        return model_capability.CapabilityProbeResult(
            "unsupported" if model == "bad" else "supported",
            datetime.now(UTC),
        )

    monkeypatch.setattr(model_capability, "probe_model_tool_capability", probe)
    async with setting_db() as db:
        first = await model_capability.verify_resolved_agent_providers(db, resolved)
        await db.commit()
        assert not isinstance(first, str)
        assert first.model == "good"
        assert first.primary.enabled_model_ids() == ["good"]
        assert first.providers[2].enabled_model_ids() == ["fallback"]
        assert calls == [(1, "good"), (1, "bad"), (2, "fallback")]

    calls.clear()
    async with setting_db() as db:
        second = await model_capability.verify_resolved_agent_providers(db, resolved)
        assert not isinstance(second, str)
        assert calls == []
        assert await db.get(SystemSetting, model_capability.CACHE_KEY) is not None


@pytest.mark.asyncio
async def test_verify_rejects_unsupported_primary(setting_db, monkeypatch) -> None:
    primary = _provider(1, models=["bad"])
    resolved = ResolvedAgentProviders(primary=primary, model="bad", providers={1: primary})

    async def probe(_provider, _model):  # noqa: ANN001
        return model_capability.CapabilityProbeResult("unsupported", datetime.now(UTC))

    monkeypatch.setattr(model_capability, "probe_model_tool_capability", probe)
    async with setting_db() as db:
        result = await model_capability.verify_resolved_agent_providers(db, resolved)
    assert isinstance(result, str)
    assert "provider-1" in result
    assert "工具调用" in result


@pytest.mark.asyncio
async def test_verify_keeps_temporarily_unavailable_fallback_candidate(
    setting_db,
    monkeypatch,
) -> None:
    primary = _provider(1, models=["primary"])
    fallback = _provider(2, models=["fallback"])
    fallback.models[0]["supports_tools"] = None
    resolved = ResolvedAgentProviders(
        primary=primary,
        model="primary",
        providers={1: primary, 2: fallback},
    )

    async def probe(provider, _model):  # noqa: ANN001
        return model_capability.CapabilityProbeResult(
            "supported" if provider.id == 1 else "unavailable",
            datetime.now(UTC),
            "LLMError" if provider.id == 2 else None,
        )

    monkeypatch.setattr(model_capability, "probe_model_tool_capability", probe)
    async with setting_db() as db:
        result = await model_capability.verify_resolved_agent_providers(db, resolved)

    assert not isinstance(result, str)
    assert result.primary.id == 1
    assert result.providers[2].enabled_model_ids() == ["fallback"]
    assert result.providers[2].models[0].get("supports_tools") is None


@pytest.mark.asyncio
async def test_verify_still_excludes_unsupported_fallback(setting_db, monkeypatch) -> None:
    primary = _provider(1, models=["primary"])
    fallback = _provider(2, models=["fallback"])
    resolved = ResolvedAgentProviders(
        primary=primary,
        model="primary",
        providers={1: primary, 2: fallback},
    )

    async def probe(provider, _model):  # noqa: ANN001
        return model_capability.CapabilityProbeResult(
            "supported" if provider.id == 1 else "unsupported",
            datetime.now(UTC),
        )

    monkeypatch.setattr(model_capability, "probe_model_tool_capability", probe)
    async with setting_db() as db:
        result = await model_capability.verify_resolved_agent_providers(db, resolved)

    assert not isinstance(result, str)
    assert set(result.providers) == {1}


@pytest.mark.asyncio
async def test_verify_non_blocking_skips_probe_and_schedules_refresh(
    setting_db,
    monkeypatch,
) -> None:
    primary = _provider(1, models=["m1"])
    resolved = ResolvedAgentProviders(primary=primary, model="m1", providers={1: primary})
    calls: list[tuple[int, str]] = []
    scheduled: list[list[tuple[int, str]]] = []

    async def probe(provider, model):  # noqa: ANN001
        calls.append((provider.id, model))
        return model_capability.CapabilityProbeResult("supported", datetime.now(UTC))

    def schedule(items):  # noqa: ANN001
        scheduled.append([(p.id, m) for p, m, _s in items])

    monkeypatch.setattr(model_capability, "probe_model_tool_capability", probe)
    monkeypatch.setattr(model_capability, "schedule_capability_refresh", schedule)

    async with setting_db() as db:
        result = await model_capability.verify_resolved_agent_providers(
            db, resolved, non_blocking=True
        )

    assert not isinstance(result, str)
    assert result.model == "m1"
    assert calls == []  # 请求路径不探测
    assert scheduled and scheduled[0] == [(1, "m1")]


# ─────────────────────────────────────────────────────
# WP-B17：临时放行必须对调用方可读
# ─────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(UTC)


def test_provisional_reason_none_when_not_provisional() -> None:
    assert model_capability.provisional_reason(None) is None
    assert (
        model_capability.provisional_reason(
            model_capability.CapabilityProbeResult("supported", _now())
        )
        is None
    )
    # unsupported 是明确结论，不是"临时放行"，不该给用户"正在确认"的话术
    assert (
        model_capability.provisional_reason(
            model_capability.CapabilityProbeResult("unsupported", _now())
        )
        is None
    )


def test_provisional_reason_covers_first_use_and_stale_unavailable() -> None:
    first_use = model_capability.provisional_reason(
        model_capability.CapabilityProbeResult("supported", _now(), provisional=True)
    )
    assert first_use and "首次" in first_use

    stale = model_capability.provisional_reason(
        model_capability.CapabilityProbeResult(
            "unavailable", _now(), "stale_unavailable", provisional=True
        )
    )
    assert stale and "暂时不可用" in stale
    # 两段话术必须能区分：一个是"还没测过"，一个是"上次测过但没过"
    assert first_use != stale


@pytest.mark.asyncio
async def test_non_blocking_records_provisional_primary(setting_db, monkeypatch) -> None:
    """无缓存 + non_blocking：主模型是临时放行，必须能被 last_primary_verification 读到。"""
    primary = _provider(1, models=["fresh"])
    resolved = ResolvedAgentProviders(
        primary=primary, model="fresh", providers={1: primary}
    )
    monkeypatch.setattr(model_capability, "schedule_capability_refresh", lambda items: None)

    async with setting_db() as db:
        result = await model_capability.verify_resolved_agent_providers(
            db, resolved, non_blocking=True
        )

    assert not isinstance(result, str)
    recorded = model_capability.last_primary_verification()
    assert recorded is not None
    assert recorded.provisional is True
    assert recorded.status == "supported"
    assert model_capability.provisional_reason(recorded)


@pytest.mark.asyncio
async def test_blocking_confirmed_primary_is_not_provisional(setting_db, monkeypatch) -> None:
    """真实探测通过后不能再自称"临时放行"，否则用户会一直看到误导性提示。"""

    async def probe(_provider, _model):  # noqa: ANN001
        return model_capability.CapabilityProbeResult("supported", _now())

    monkeypatch.setattr(model_capability, "probe_model_tool_capability", probe)
    primary = _provider(1, models=["m1"])
    resolved = ResolvedAgentProviders(
        primary=primary, model="m1", providers={1: primary}
    )

    async with setting_db() as db:
        result = await model_capability.verify_resolved_agent_providers(db, resolved)

    assert not isinstance(result, str)
    recorded = model_capability.last_primary_verification()
    assert recorded is not None
    assert recorded.provisional is False
    assert model_capability.provisional_reason(recorded) is None


@pytest.mark.asyncio
async def test_primary_verification_does_not_leak_across_concurrent_turns(
    setting_db,
    monkeypatch,
) -> None:
    """并发会话必须各自读到自己的结论：A 轮临时放行不能污染 B 轮的提示。"""
    monkeypatch.setattr(model_capability, "schedule_capability_refresh", lambda items: None)
    primary = _provider(1, models=["fresh"])
    resolved = ResolvedAgentProviders(
        primary=primary, model="fresh", providers={1: primary}
    )

    async def turn() -> model_capability.CapabilityProbeResult | None:
        async with setting_db() as db:
            await model_capability.verify_resolved_agent_providers(
                db, resolved, non_blocking=True
            )
        return model_capability.last_primary_verification()

    # asyncio.gather 的每个协程有独立 context 副本，ContextVar 写入互不可见
    first, second = await asyncio.gather(turn(), turn())
    assert first is not None and first.provisional is True
    assert second is not None and second.provisional is True


# ─────────── 后台刷新任务强引用 & 并发探测局部 upsert ───────────


@pytest.mark.asyncio
async def test_schedule_capability_refresh_keeps_strong_reference(monkeypatch) -> None:
    """后台刷新任务必须登记到模块级集合并随完成移除（事件循环只持弱引用）。"""

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_refresh(_items):
        started.set()
        await release.wait()

    monkeypatch.setattr(model_capability, "_refresh_capability_cache_background", fake_refresh)

    model_capability.schedule_capability_refresh(
        [(_provider(1, models=["m1"]), "m1", "sig-1")]
    )
    await started.wait()
    # 任务存活期间已登记（持强引用，不会被 GC）
    assert len(model_capability._REFRESH_TASKS) == 1
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert model_capability._REFRESH_TASKS == set()  # done_callback 已移除


@pytest.mark.asyncio
async def test_concurrent_probes_merge_distinct_signatures(setting_db, monkeypatch) -> None:
    """并发探测不同 signature 不能互相覆盖：A 写回前挂起、B 先完成，A 恢复后两者都在。"""

    provider_a = _provider(1, models=["model-a"])
    provider_b = _provider(2, models=["model-b"])
    sig_a = model_capability._provider_signature(provider_a, "model-a")
    sig_b = model_capability._provider_signature(provider_b, "model-b")
    assert sig_a != sig_b

    db_a = setting_db()
    a_probed = asyncio.Event()
    release_a = asyncio.Event()

    async def probe(provider, _model):  # noqa: ANN001
        if provider.id == 1:
            result = model_capability.CapabilityProbeResult("supported", datetime.now(UTC))
            a_probed.set()
            await release_a.wait()
            return result
        return model_capability.CapabilityProbeResult("unsupported", datetime.now(UTC))

    monkeypatch.setattr(model_capability, "probe_model_tool_capability", probe)

    resolved = ResolvedAgentProviders(
        primary=provider_a,
        model="model-a",
        providers={1: provider_a},
    )
    task_a = asyncio.create_task(
        model_capability.verify_resolved_agent_providers(db_a, resolved)
    )
    await a_probed.wait()

    # A 挂在写回前：B 走后台刷新路径，完整写入 sig_b
    import app.db.base as db_base

    monkeypatch.setattr(db_base, "AsyncSessionLocal", setting_db)
    await model_capability._refresh_capability_cache_background(
        [(provider_b, "model-b", sig_b)]
    )
    async with setting_db() as db:
        committed = await db.get(SystemSetting, model_capability.CACHE_KEY)
        assert sig_b in committed.value["entries"]

    # 放行 A：写回必须重读现值、只 upsert sig_a，不得丢掉 B 的结果
    release_a.set()
    result_a = await task_a
    await db_a.close()
    assert not isinstance(result_a, str)

    async with setting_db() as db:
        row = await db.get(SystemSetting, model_capability.CACHE_KEY)
        entries = row.value["entries"]
    assert sig_a in entries
    assert sig_b in entries
    assert entries[sig_b]["status"] == "unsupported"
    assert entries[sig_a]["status"] == "supported"


@pytest.mark.asyncio
async def test_upsert_cache_entries_refreshes_stale_identity_map_row(setting_db) -> None:
    """upsert 的"重读现值"必须穿透会话 identity map，否则合并到过期快照上。

    真实场景：请求路径先读过缓存（行对象进入会话身份映射），探测 await 期间
    并发会话写入了新条目；旧实现的 db.get 命中身份映射拿到旧行对象，合并写回
    把并发条目整体覆盖丢失。
    """
    store: dict = {}
    store[model_capability.CACHE_KEY] = {
        "version": 1,
        "entries": {
            "sig_old": {"status": "supported", "checked_at": "2026-10-24T00:00:00+00:00"},
        },
    }
    async with setting_db() as seed_db:
        seed_db.add(SystemSetting(key=model_capability.CACHE_KEY, value=store[model_capability.CACHE_KEY]))
        await seed_db.commit()
    db = setting_db()
    # 第一次读：行对象（含 sig_old）进入身份映射
    row = await db.get(SystemSetting, model_capability.CACHE_KEY)
    assert row is not None

    # 并发会话已提交的新状态：store 里多了一个新 signature 条目
    concurrent = {
        "version": 1,
        "entries": {
            "sig_old": {"status": "supported", "checked_at": "2026-10-24T00:00:00+00:00"},
            "sig_concurrent": {"status": "unsupported", "checked_at": "2026-10-24T00:01:00+00:00"},
        },
    }
    async with setting_db() as concurrent_db:
        concurrent_row = await concurrent_db.get(SystemSetting, model_capability.CACHE_KEY)
        concurrent_row.value = concurrent
        await concurrent_db.commit()

    # 本会话 upsert：必须 refresh 重读，把 sig_concurrent 一并保留
    await model_capability._upsert_cache_entries(
        db,
        {"sig_new": {"status": "supported", "checked_at": "2026-10-24T00:02:00+00:00"}},
    )
    await db.commit()
    await db.close()

    async with setting_db() as verification_db:
        current = await verification_db.get(SystemSetting, model_capability.CACHE_KEY)
        entries = current.value["entries"]
    assert "sig_new" in entries
    assert "sig_concurrent" in entries
    assert entries["sig_concurrent"]["status"] == "unsupported"
    assert "sig_old" in entries
