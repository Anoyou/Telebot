from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models.command import LLMProvider
from app.services import command_service
from app.services.llm_agent import AgentCallbacks, AgentLimits, AgentTool, run_agent
from app.services.llm_protocol import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from app.services.system_agent import provider_verify
from app.services.system_agent.context import ToolContext
from app.services.system_agent.redactor import summarize_tool_result
from app.services.system_agent.registry import ActionKeepPendingError, PreparedAction, ToolRegistry
from app.services.system_agent.skills.catalog import BUILTIN_SKILLS
from app.services.system_agent.tools.providers import (
    list_providers,
    probe_and_add_preview,
    register,
    save_execute,
    save_precheck,
    save_preview,
    verify_execute,
    verify_precheck,
    verify_preview,
)


def _empty_result() -> SimpleNamespace:
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


@pytest.mark.asyncio
async def test_list_providers_always_lists_all_without_filters() -> None:
    db = AsyncMock()
    db.execute.return_value = _empty_result()

    await list_providers(
        ToolContext(db=db, channel="web", role="viewer"),
        {"id": 23, "name": "metapi-gpt", "limit": 100},
    )

    statement = db.execute.await_args.args[0]
    assert len(statement._where_criteria) == 0


def test_provider_list_schema_supports_compact_model_pagination() -> None:
    registry = ToolRegistry()
    register(registry)

    spec = next(item for item in registry.list_all() if item.name == "providers.list")
    properties = spec.input_schema["properties"]
    assert set(properties) == {"view", "limit", "offset", "strip_prefix"}
    assert properties["view"]["enum"] == ["providers", "models"]
    assert properties["view"]["default"] == "providers"
    assert properties["offset"]["minimum"] == 0
    assert properties["limit"]["type"] == "integer"
    assert properties["strip_prefix"]["default"] is False
    assert "required" not in spec.input_schema
    assert spec.input_schema["additionalProperties"] is False
    assert "全部模型提供商" in spec.description
    assert "next_offset" in spec.description
    assert "has_more=false" in spec.description


def _provider_row(provider_id: int, models: list) -> LLMProvider:
    return LLMProvider(
        id=provider_id,
        name=f"Provider {provider_id}",
        provider="openai",
        base_url="https://example.test/v1",
        default_model=f"p{provider_id}/default-only",
        api_key_enc="encrypted-api-key-not-for-output",
        request_headers_enc="encrypted-header-not-for-output",
        models=models,
        cost_tier=2,
        notes="private-provider-notes",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("view", (None, "providers"))
async def test_provider_list_default_view_preserves_configuration_without_secrets(view) -> None:
    row = _provider_row(
        1,
        [{"id": "p1/chat", "enabled": False, "supports_tools": True}],
    )
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [row])
    )
    result = await list_providers(
        ToolContext(db=db, channel="web", role="viewer"),
        {} if view is None else {"view": view},
    )

    assert set(result) == {"count", "providers", "note"}
    assert result["count"] == 1
    provider = result["providers"][0]
    assert provider["id"] == 1
    assert provider["name"] == "Provider 1"
    assert provider["base_url"] == row.base_url
    assert provider["default_model"] == row.default_model
    assert provider["models"] == [
        {"id": "p1/chat", "enabled": False, "supports_tools": True}
    ]
    assert provider["has_api_key"] is True
    assert provider["notes"] == row.notes
    assert "encrypted-api-key-not-for-output" not in json.dumps(result)
    assert "encrypted-header-not-for-output" not in json.dumps(result)
    assert db.execute.await_args.args[0]._limit_clause.value == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("strip_prefix", (False, True))
async def test_model_pages_cover_all_nine_providers_without_truncation(strip_prefix) -> None:
    rows = [
        _provider_row(
            provider_id,
            [
                {"id": f"p{provider_id}/model-{index:03d}", "enabled": index % 2 == 0}
                for index in range(240)
            ]
            + [
                {"id": "shared/model-shared"},
                {"id": f"p{provider_id}/model-000"},
                {"id": " "},
                {},
                None,
            ],
        )
        for provider_id in range(1, 10)
    ]
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        all=lambda: rows, scalars=lambda: SimpleNamespace(all=lambda: rows)
    )
    ctx = ToolContext(db=db, channel="web", role="viewer")
    legacy = await list_providers(ctx, {})
    assert summarize_tool_result(legacy, max_chars=8000)["truncated"] is True
    expected = {
        f"p{provider_id}/model-{index:03d}"
        for provider_id in range(1, 10)
        for index in range(240)
    } | {f"p{provider_id}/default-only" for provider_id in range(1, 10)} | {"shared/model-shared"}
    if strip_prefix:
        expected = {name.rsplit("/", 1)[-1] for name in expected}

    collected: list[str] = []
    offset = 0
    for _ in range(len(expected) + 1):
        page = await list_providers(
            ctx,
            {"view": "models", "strip_prefix": strip_prefix, "offset": offset, "limit": 37},
        )
        assert page["view"] == "models"
        assert page["offset"] == offset
        assert page["limit"] == 37
        assert page["strip_prefix"] is strip_prefix
        assert page["total"] == len(expected)
        assert page["count"] == len(page["models"])
        assert 0 < page["count"] <= 37
        assert len(json.dumps(page, ensure_ascii=False)) < 6500
        assert summarize_tool_result(page, max_chars=8000) == page
        assert set(page) == {
            "view", "models", "count", "offset", "limit", "total",
            "has_more", "next_offset", "strip_prefix",
        }
        assert "encrypted-api-key-not-for-output" not in str(page)
        assert "encrypted-header-not-for-output" not in str(page)
        assert "private-provider-notes" not in str(page)
        collected.extend(page["models"])
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] == offset + page["count"]
        offset = page["next_offset"]
    else:
        pytest.fail("模型分页未终止")

    assert collected == sorted(expected)
    assert len(collected) == len(set(collected))
    statement = db.execute.await_args.args[0]
    assert [column.key for column in statement.selected_columns] == ["default_model", "models"]
    assert statement._limit_clause is None


@pytest.mark.asyncio
async def test_model_pagination_respects_character_budget_with_long_escaped_names() -> None:
    names = [f'model-{index:03d}-' + '长模型\\"' * 40 for index in range(210)]
    row = _provider_row(1, [{"id": name} for name in names])
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [row])
    ctx = ToolContext(db=db, channel="web", role="viewer")
    collected: list[str] = []
    offset = 0
    while True:
        page = await list_providers(ctx, {"view": "models", "offset": offset, "limit": 200})
        assert 0 < page["count"] < 200
        assert len(json.dumps(page, ensure_ascii=False)) < 6500
        assert summarize_tool_result(page, max_chars=8000) == page
        collected.extend(page["models"])
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert collected == sorted([row.default_model, *names])


@pytest.mark.asyncio
async def test_model_names_strip_last_prefix_before_dedup_and_include_default() -> None:
    rows = [
        _provider_row(1, [
            {"id": " vendor/nested/chat "}, {"id": "other/chat"}, {"id": "chat"},
            {"id": "other/"}, {"id": ""}, {}, None,
        ]),
        _provider_row(2, []),
    ]
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: rows)
    ctx = ToolContext(db=db, channel="web", role="viewer")
    page = await list_providers(ctx, {"view": "models", "strip_prefix": True})
    assert page["models"] == ["chat", "default-only"]
    assert page["total"] == 2
    assert page["has_more"] is False
    assert page["next_offset"] is None
    assert page["limit"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", (0, 1000))
async def test_model_pages_empty_and_out_of_range(offset) -> None:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [])
    page = await list_providers(
        ToolContext(db=db, channel="web", role="viewer"),
        {"view": "models", "offset": offset},
    )
    assert page["models"] == []
    assert page["count"] == page["total"] == 0
    assert page["has_more"] is False
    assert page["next_offset"] is None


@pytest.mark.asyncio
async def test_model_pages_report_end_after_last_model() -> None:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [_provider_row(1, [])])
    page = await list_providers(
        ToolContext(db=db, channel="web", role="viewer"),
        {"view": "models", "offset": 5},
    )
    assert page["total"] == 1
    assert page["count"] == 0
    assert page["has_more"] is False
    assert page["next_offset"] is None


@pytest.mark.asyncio
async def test_model_query_agent_stops_at_step_budget_and_resumes_from_summary_cursor() -> None:
    rows = []
    for provider_id in range(9):
        models = [
            {"id": f"vendor-{provider_id}/model-{index:04d}"}
            for index in range(provider_id * 100, (provider_id + 1) * 100)
        ]
        # 同一模型的默认配置与重复记录不能导致分页重复。
        row = _provider_row(provider_id + 1, [*models, models[0]])
        row.default_model = models[0]["id"]
        rows.append(row)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: rows)
    ctx = ToolContext(db=db, channel="web", role="viewer")
    registry = ToolRegistry()
    register(registry)
    provider_spec = next(spec for spec in registry.list_all() if spec.name == "providers.list")
    spec = ToolSpec(provider_spec.name, provider_spec.description, provider_spec.input_schema)
    skill = next(skill for skill in BUILTIN_SKILLS if skill.name == "ai-config")
    offsets: list[int] = []
    memory_cursors: list[dict] = []
    model_requests: list[ModelRequest] = []

    async def handler(arguments: dict) -> dict:
        offsets.append(arguments["offset"])
        page = await list_providers(ctx, arguments)
        assert summarize_tool_result(page, max_chars=8000) == page
        return page

    async def on_tool_finish(_call: ToolCall, result: ToolResult) -> None:
        assert not result.is_error
        page = result.content
        # 使用与 runtime 事件和记忆一致的摘要门禁，直接检查被截断的 preview。
        for max_chars in (1200, 500):
            summary = summarize_tool_result(page, max_chars=max_chars)
            assert summary["truncated"] is True
            preview = summary["preview"]
            for key in ("total", "has_more", "next_offset", "offset", "limit", "strip_prefix"):
                assert f'"{key}": {json.dumps(page[key])}' in preview
            assert preview.index('"next_offset":') < preview.index('"models": [')
        # 只从短记忆摘要恢复元数据，不能依赖未截断的原模型数组续查。
        memory_cursors.append(json.loads(preview.split(', "models":', 1)[0] + "}"))

    async def model_call(request: ModelRequest) -> ModelResponse:
        model_requests.append(request)
        pages = [
            result.content
            for message in request.messages
            for result in message.tool_results
        ]
        last_page = pages[-1] if pages else None
        if not request.tools or (last_page is not None and not last_page["has_more"]):
            assert last_page is not None
            report = {
                "status": "分段未完成" if last_page["has_more"] else "剩余段完成",
                "next_offset": last_page["next_offset"],
                "total": last_page["total"],
                "models": [name for page in pages for name in page["models"]],
            }
            return ModelResponse(model="test", content=(TextContent(json.dumps(report)),))
        cursor = last_page or json.loads(request.messages[-1].text_content())
        offset = cursor["next_offset"]
        return ModelResponse(
            model="test",
            tool_calls=(ToolCall(f"page-{offset}", spec.name, {
                "view": "models", "limit": 200, "strip_prefix": True, "offset": offset,
            }),),
        )

    tools = {spec.name: AgentTool(spec, handler, read_only=True)}
    callbacks = AgentCallbacks(on_tool_finish=on_tool_finish)
    system = ModelMessage.text(MessageRole.SYSTEM, "\n".join(skill.instructions))
    first = await run_agent(
        model_call,
        ModelRequest(model="test", messages=(
            system, ModelMessage.text(MessageRole.USER, '{"next_offset": 0}'),
        ), tools=(spec,)),
        tools,
        limits=AgentLimits(max_steps=3),
        callbacks=callbacks,
    )
    assert first.steps == first.tool_calls == 3
    assert offsets == [0, 200, 400]
    assert len(model_requests) == 4  # 三步工具调用后仅允许一次无工具总结。
    assert model_requests[-1].tools == ()
    first_delivery = json.loads(first.text)
    assert first_delivery["status"] == "分段未完成"
    assert first_delivery["next_offset"] == 600
    assert first_delivery["total"] == 900
    assert first_delivery["models"] == [f"model-{index:04d}" for index in range(600)]
    assert memory_cursors[-1]["has_more"] is True
    assert memory_cursors[-1]["next_offset"] == 600

    # 用户在新一轮明确要求继续；没有自动加步数或自动重启循环。
    resumed = await run_agent(
        model_call,
        ModelRequest(model="test", messages=(
            system,
            ModelMessage.text(MessageRole.USER, json.dumps({
                "request": "继续", **memory_cursors[-1],
            })),
        ), tools=(spec,)),
        tools,
        limits=AgentLimits(max_steps=3),
        callbacks=callbacks,
    )
    assert resumed.steps == 3
    assert resumed.tool_calls == 2
    assert offsets == [0, 200, 400, 600, 800]
    assert len(model_requests) == 7
    remaining_delivery = json.loads(resumed.text)
    assert remaining_delivery["status"] == "剩余段完成"
    assert remaining_delivery["next_offset"] is None
    assert remaining_delivery["total"] == 900
    assert remaining_delivery["models"] == [f"model-{index:04d}" for index in range(600, 900)]
    # 验证用户实际收到的两段，而非依赖测试进程暗中保留的完整工具结果。
    delivered = first_delivery["models"] + remaining_delivery["models"]
    assert delivered == [f"model-{index:04d}" for index in range(900)]
    assert len(delivered) == len(set(delivered)) == 900


def test_probe_and_add_tool_is_a_confirmed_write_with_encrypted_secret() -> None:
    registry = ToolRegistry()
    register(registry)

    spec = next(item for item in registry.list_all() if item.name == "providers.probe_and_add")
    assert spec.read_only is False
    assert spec.secret_argument_names == ("api_key",)
    assert spec.allow_secret_input is False
    assert spec.preview_handler is probe_and_add_preview
    assert spec.execute_handler is not None
    assert "测活成功" in spec.description
    assert spec.input_schema["required"] == ["base_url"]
    assert "request_headers" not in spec.input_schema["properties"]


@pytest.mark.parametrize(
    "handler",
    (
        probe_and_add_preview,
        save_preview,
        save_precheck,
        save_execute,
        verify_preview,
        verify_precheck,
        verify_execute,
    ),
)
@pytest.mark.asyncio
async def test_provider_tools_reject_undeclared_request_headers_at_runtime(handler) -> None:
    with pytest.raises(ValueError, match="本地安全策略拒绝，尚未向上游发起请求"):
        await handler(
            None,
            {
                "request_headers": [
                    {
                        "name": "X-Tenant-ID",
                        "value": "opaque-header-secret",
                        "scopes": ["inference"],
                    }
                ]
            },
        )


@pytest.mark.asyncio
async def test_probe_and_add_discovers_model_and_prepares_confirmation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_verify(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {
            "ok": True,
            "model": "upstream-model-alias",
            "requested_model": "chat-model",
            "latency_ms": 123,
            "api_format": "chat_completions",
            "base_url": "https://api.example/v1",
            "provider": "openai",
            "suggested_name": "api.example",
            "response_preview": "sk-secret-value",
        }

    monkeypatch.setattr(provider_verify, "run_quick_verify", fake_verify)
    args = {
        "base_url": "https://api.example/v1",
        "api_key": "sk-secret-value",
    }

    prepared = await probe_and_add_preview(
        ToolContext(db=AsyncMock(), channel="web", role="admin"),
        args,
    )

    assert captured["api_key"] == "sk-secret-value"
    assert captured["default_model"] is None
    assert args == {
        "base_url": "https://api.example/v1",
        "api_key": "sk-secret-value",
    }
    assert isinstance(prepared, PreparedAction)
    assert prepared.arguments == {
        "base_url": "https://api.example/v1",
        "api_key": "sk-secret-value",
        "name": "api.example",
        "provider": "openai",
        "default_model": "chat-model",
            "api_format": "chat_completions",
            "models": [{"id": "chat-model", "enabled": True, "custom": False}],
        }
    preview = prepared.preview
    assert preview["mode"] == "verified_create"
    assert preview["provider"]["default_model"] == "chat-model"
    assert preview["liveness"]["ok"] is True
    assert "尚未保存" in preview["note"]
    assert "sk-secret-value" not in str(preview)


@pytest.mark.asyncio
async def test_probe_and_add_failure_does_not_prepare_confirmation(monkeypatch) -> None:
    async def fake_verify(**_kwargs):  # noqa: ANN003
        raise ActionKeepPendingError(
            "Provider 验证失败：上游不可用。临时密钥仍安全暂存。",
            code="PROVIDER_VERIFY_FAILED",
        )

    monkeypatch.setattr(provider_verify, "run_quick_verify", fake_verify)
    args = {
        "base_url": "https://api.example/v1",
        "api_key": "sk-secret-value",
    }

    with pytest.raises(ActionKeepPendingError):
        await probe_and_add_preview(
            ToolContext(db=AsyncMock(), channel="web", role="admin"),
            args,
        )

    assert "name" not in args
    assert "default_model" not in args


@pytest.mark.asyncio
async def test_gateway_provider_update_marks_precommit_candidate_sync(monkeypatch) -> None:
    row = SimpleNamespace(id=7, execution_backend="codex_gateway")
    action = SimpleNamespace(arguments={})
    db = AsyncMock()
    db.get = AsyncMock(return_value=row)
    update = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda: {"id": 7, "name": "gateway"})
    )
    monkeypatch.setattr(command_service, "update_provider", update)

    ctx = ToolContext(db=db, channel="web", role="admin", action=action)
    await save_execute(
        ctx,
        {"id": 7, "notes": "updated"},
    )

    assert ctx.gateway_candidate_sync is True


@pytest.mark.asyncio
async def test_probe_and_add_rejects_upstream_model_field_that_echoes_key(monkeypatch) -> None:
    key = "AbCdEfGhIjKlMnOpQrStUvWxYz123456"

    async def fake_verify(**_kwargs):  # noqa: ANN003
        return {
            "ok": True,
            "model": key,
            "requested_model": key,
            "latency_ms": 10,
            "api_format": "chat_completions",
            "base_url": "https://api.example/v1",
            "provider": "openai",
            "suggested_name": "api.example",
            "response_preview": key,
        }

    monkeypatch.setattr(provider_verify, "run_quick_verify", fake_verify)

    with pytest.raises(ValueError, match="公开配置字段包含当前凭据"):
        await probe_and_add_preview(
            ToolContext(db=AsyncMock(), channel="web", role="admin"),
            {"base_url": "https://api.example/v1", "api_key": key},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_event", "expected_code", "expected_message"),
    (
        (
            {
                "error": "上游 HTTP 400：Unsupported parameter: max_output_tokens",
                "status_code": 502,
                "upstream_status_code": 400,
                "error_category": "request_invalid",
            },
            "PROVIDER_VERIFY_FAILED",
            "无需重新输入 API Key",
        ),
        (
            {
                "error": "HTTP 401：invalid api key",
                "status_code": 401,
                "error_category": "auth_failed",
            },
            "API_KEY_REJECTED",
            "如需更换密钥，请重新输入",
        ),
    ),
)
async def test_saved_provider_verify_distinguishes_auth_from_upstream_failure(
    monkeypatch,
    error_event: dict[str, object],
    expected_code: str,
    expected_message: str,
) -> None:
    async def fake_events(**kwargs):  # noqa: ANN003
        yield {"type": "error", "ok": False, **error_event}

    monkeypatch.setattr(provider_verify.llm_quick_verify, "quick_verify_events", fake_events)
    monkeypatch.setattr(
        provider_verify.llm_quick_verify,
        "normalize_quick_verify_base_url",
        lambda value: value,
    )

    with pytest.raises(ActionKeepPendingError) as ei:
        await provider_verify.run_quick_verify(
            base_url="https://api.example/v1",
            api_key="sk-secret",
            api_format="responses",
            default_model="deepseek-chat",
            provider="openai",
            using_saved_key=True,
        )

    assert ei.value.code == expected_code
    if expected_code == "API_KEY_REJECTED":
        assert ei.value.clear_secret_names == ("api_key",)
    else:
        assert ei.value.clear_secret_names == ()
    assert expected_message in ei.value.message
    assert "已保存的 Provider 配置未修改" in ei.value.message


@pytest.mark.asyncio
async def test_saved_provider_verify_does_not_parse_auth_status_from_wrapper_text(
    monkeypatch,
) -> None:
    async def fake_events(**kwargs):  # noqa: ANN003
        yield {
            "type": "error",
            "ok": False,
            "error": "包装层记录了 401，但没有返回结构化上游状态",
        }

    monkeypatch.setattr(provider_verify.llm_quick_verify, "quick_verify_events", fake_events)
    monkeypatch.setattr(
        provider_verify.llm_quick_verify,
        "normalize_quick_verify_base_url",
        lambda value: value,
    )

    with pytest.raises(ActionKeepPendingError) as ei:
        await provider_verify.run_quick_verify(
            base_url="https://api.example/v1",
            api_key="sk-secret",
            api_format="responses",
            default_model="deepseek-chat",
            provider="openai",
            using_saved_key=True,
        )

    assert ei.value.code == "PROVIDER_VERIFY_FAILED"
    assert ei.value.clear_secret_names == ()
