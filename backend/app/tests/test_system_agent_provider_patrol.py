"""System Agent 模型巡检工具：providers.patrol / patrol_status / patrol_cancel。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import commands as commands_api
from app.schemas.command import (
    FullLivenessPreviewResponse,
    FullLivenessRunResponse,
    FullLivenessRunStartResponse,
    LivenessProviderPlan,
    LivenessResultItem,
)
from app.services.system_agent.context import ToolContext
from app.services.system_agent.registry import (
    PreparedAction,
    get_registry,
    reset_registry_for_tests,
)
from app.services.system_agent.skills.catalog import BUILTIN_SKILLS
from app.services.system_agent.skills.registry import SkillRegistry
from app.services.system_agent.tool_routing import route_locally, select_tool_specs, tool_domain
from app.services.system_agent.tools import provider_patrol


def _plan(pid: int, name: str, models: list[str], *, executable: bool = True, reason: str | None = None):
    return LivenessProviderPlan(
        provider_id=pid,
        provider_name=name,
        enabled_models=models,
        executable=executable,
        skipped_reason=reason,
    )


def _preview(plans: list[LivenessProviderPlan]) -> FullLivenessPreviewResponse:
    executable = [p for p in plans if p.executable]
    total = sum(len(p.enabled_models) for p in executable)
    return FullLivenessPreviewResponse(
        provider_total=len(plans),
        executable_provider_total=len(executable),
        enabled_model_total=sum(len(p.enabled_models) for p in plans),
        task_total=total,
        max_tokens=256,
        max_output_tokens=256,
        global_concurrency=8,
        provider_concurrency=2,
        needs_confirmation=total > 30,
        providers=plans,
    )


def _ctx(db=None, *, channel: str = "web", role: str = "admin", session=None) -> ToolContext:
    return ToolContext(db=db or AsyncMock(), channel=channel, role=role, session=session, web_user_id=1)


@pytest.fixture
def fake_api(monkeypatch):
    state: dict = {
        "plans": [
            _plan(1, "OpenAI", ["gpt-a", "gpt-b"]),
            _plan(2, "Claude", ["claude-x"]),
            _plan(3, "NoKey", ["m"], executable=False, reason="config_error"),
        ],
        "preview_calls": [],
        "run_calls": [],
        "status_calls": [],
        "cancel_calls": [],
    }

    async def fake_preview(payload, db, _user):
        state["preview_calls"].append(payload)
        plans = [
            p
            for p in state["plans"]
            if not payload.only_provider_ids or p.provider_id in payload.only_provider_ids
        ]
        if payload.models_by_provider is not None:
            plans = [
                p.model_copy(
                    update={
                        "enabled_models": [
                            m
                            for m in p.enabled_models
                            if m in payload.models_by_provider.get(p.provider_id, [])
                        ]
                    }
                )
                for p in plans
            ]
            plans = [
                p.model_copy(update={"executable": p.executable and bool(p.enabled_models)})
                for p in plans
            ]
        return _preview(plans)

    async def fake_run(payload, db, _user):
        state["run_calls"].append(payload)
        return FullLivenessRunStartResponse(run_id="run-1", status="queued", task_total=3)

    async def fake_status(run_id, _user):
        state["status_calls"].append(run_id)
        if run_id != "run-1":
            raise HTTPException(status_code=404, detail="测活运行不存在或已过期")
        return state.get("snapshot") or FullLivenessRunResponse(
            run_id="run-1", status="running", task_total=3, completed=0
        )

    async def fake_cancel(run_id, _user):
        state["cancel_calls"].append(run_id)
        return FullLivenessRunResponse(run_id=run_id, status="running", task_total=3, completed=1)

    monkeypatch.setattr(commands_api, "full_liveness_preview", fake_preview)
    monkeypatch.setattr(commands_api, "full_liveness_run", fake_run)
    monkeypatch.setattr(commands_api, "full_liveness_status", fake_status)
    monkeypatch.setattr(commands_api, "full_liveness_cancel", fake_cancel)
    return state


def test_patrol_tools_registered_for_web_and_bot_with_confirmation() -> None:
    reset_registry_for_tests()
    registry = get_registry()

    patrol = registry.get("providers.patrol")
    assert patrol is not None
    assert patrol.read_only is False  # 走待确认 Action
    assert patrol.min_role == "admin"
    assert patrol.runtime_retryable is False
    assert patrol.preview_handler is provider_patrol.patrol_preview
    assert patrol.execute_handler is provider_patrol.patrol_execute
    assert set(patrol.channels) == {"web", "bot"}
    assert patrol.input_schema["additionalProperties"] is False
    assert "额度" in patrol.description

    status = registry.get("providers.patrol_status")
    assert status is not None and status.read_only is True
    assert set(status.channels) == {"web", "bot"}

    cancel = registry.get("providers.patrol_cancel")
    assert cancel is not None and cancel.read_only is False
    assert cancel.min_role == "admin"

    for channel in ("web", "bot"):
        names = {s.name for s in registry.list_for(channel=channel, role="admin")}
        assert {"providers.patrol", "providers.patrol_status", "providers.patrol_cancel"} <= names
    viewer = {s.name for s in registry.list_for(channel="bot", role="viewer")}
    assert "providers.patrol" not in viewer
    assert "providers.patrol_status" in viewer


@pytest.mark.asyncio
async def test_patrol_preview_reuses_web_preview_and_does_not_start_run(fake_api) -> None:
    prepared = await provider_patrol.patrol_preview(_ctx(), {})

    assert isinstance(prepared, PreparedAction)
    assert fake_api["run_calls"] == []  # 预览阶段不调用上游
    assert fake_api["preview_calls"][0].only_provider_ids is None
    assert prepared.arguments["approved_task_total"] == 3
    preview = prepared.preview
    assert preview["task_total"] == 3
    assert preview["executable_provider_total"] == 2
    assert "全部 Provider" in preview["summary"]
    assert "消耗" in preview["warning"] and "不会自动重试" in preview["warning"]
    skipped = next(p for p in preview["providers"] if p["provider_id"] == 3)
    assert skipped["executable"] is False and skipped["skipped_reason"] == "config_error"


@pytest.mark.asyncio
async def test_patrol_preview_scopes_providers_and_models(fake_api) -> None:
    prepared = await provider_patrol.patrol_preview(
        _ctx(), {"provider_ids": [1, 2], "models": ["gpt-b", "claude-x"]}
    )

    assert prepared.preview["task_total"] == 2
    assert prepared.arguments["provider_ids"] == [1, 2]
    assert prepared.arguments["models"] == ["gpt-b", "claude-x"]
    second = fake_api["preview_calls"][-1]
    assert second.models_by_provider == {1: ["gpt-b"], 2: ["claude-x"]}


@pytest.mark.asyncio
async def test_patrol_preview_rejects_missing_provider_and_empty_scope(fake_api) -> None:
    with pytest.raises(ValueError, match="#99"):
        await provider_patrol.patrol_preview(_ctx(), {"provider_ids": [1, 99]})
    with pytest.raises(ValueError, match="没有可巡检"):
        await provider_patrol.patrol_preview(_ctx(), {"provider_ids": [3]})
    assert fake_api["run_calls"] == []


@pytest.mark.asyncio
async def test_patrol_preview_converts_ai_disabled_http_error(monkeypatch) -> None:
    async def disabled(payload, db, _user):
        raise HTTPException(status_code=409, detail={"code": "AI_DISABLED", "message": "AI 能力已关闭"})

    monkeypatch.setattr(commands_api, "full_liveness_preview", disabled)
    with pytest.raises(ValueError, match="AI 能力已关闭"):
        await provider_patrol.patrol_preview(_ctx(), {})


@pytest.mark.asyncio
async def test_patrol_execute_starts_shared_job_with_confirmed_large_run(fake_api) -> None:
    result = await provider_patrol.patrol_execute(
        _ctx(), {"provider_ids": [1], "approved_task_total": 2, "account_id": 7, "timeout_seconds": 5}
    )

    assert result["run_id"] == "run-1"
    assert result["business_changed"] is False
    assert len(fake_api["run_calls"]) == 1
    payload = fake_api["run_calls"][0]
    assert payload.confirm_large_run is True
    assert payload.only_provider_ids == [1]
    assert payload.timeout_seconds == 10  # 下限钳制


@pytest.mark.asyncio
async def test_patrol_execute_refuses_when_scope_grew_after_confirmation(fake_api) -> None:
    fake_api["plans"][0] = _plan(1, "OpenAI", ["gpt-a", "gpt-b", "gpt-c", "gpt-d"])

    with pytest.raises(ValueError, match="范围已扩大"):
        await provider_patrol.patrol_execute(_ctx(), {"approved_task_total": 3})
    assert fake_api["run_calls"] == []


@pytest.mark.asyncio
async def test_patrol_execute_surfaces_busy_without_retry(fake_api, monkeypatch) -> None:
    calls = []

    async def busy(payload, db, _user):
        calls.append(payload)
        raise HTTPException(status_code=429, detail={"code": "LIVENESS_BUSY", "message": "已有 2 个测活任务运行中"})

    monkeypatch.setattr(commands_api, "full_liveness_run", busy)
    with pytest.raises(ValueError, match="LIVENESS_BUSY"):
        await provider_patrol.patrol_execute(_ctx(), {"approved_task_total": 3})
    assert len(calls) == 1  # 不自动重试


def _item(pid, name, model, status, **kw) -> LivenessResultItem:
    return LivenessResultItem(provider_id=pid, provider_name=name, model_id=model, status=status, **kw)


@pytest.mark.asyncio
async def test_patrol_status_groups_by_provider_and_redacts(fake_api) -> None:
    fake_api["snapshot"] = FullLivenessRunResponse(
        run_id="run-1",
        status="completed",
        task_total=4,
        completed=4,
        healthy=2,
        failed=2,
        results=[
            _item(1, "OpenAI", "gpt-a", "healthy", latency_ms=320, preview="hi"),
            _item(
                1,
                "OpenAI",
                "gpt-b",
                "auth_failed",
                latency_ms=90,
                status_code=401,
                upstream_status_code=401,
                error="鉴权失败 key=sk-abcdefghijklmnopqrstuvwxyz123456",
                error_category="auth_failed",
                suggestion="检查 API Key",
            ),
            _item(2, "Claude", "claude-x", "healthy", latency_ms=500),
            _item(2, "Claude", "claude-y", "timeout", latency_ms=90000, error="请求超时" * 200),
        ],
    )

    out = await provider_patrol.patrol_status(_ctx(), {"run_id": "run-1"})

    assert out["found"] is True and out["finished"] is True
    assert out["healthy"] == 2 and out["failed"] == 2
    groups = {g["provider_id"]: g for g in out["providers"]}
    openai = groups[1]
    assert openai["healthy"] == 1 and openai["failed"] == 1
    failed = openai["models"][0]  # 失败优先
    assert failed["model"] == "gpt-b" and failed["ok"] is False
    assert failed["http_status"] == 401 and failed["error_category"] == "auth_failed"
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in failed["error"]
    ok = openai["models"][1]
    assert ok == {"model": "gpt-a", "ok": True, "status": "healthy", "latency_ms": 320, "http_status": None}
    timeout = groups[2]["models"][0]
    assert len(timeout["error"]) <= 240


@pytest.mark.asyncio
async def test_patrol_status_only_failed_and_limit(fake_api) -> None:
    fake_api["snapshot"] = FullLivenessRunResponse(
        run_id="run-1",
        status="completed",
        task_total=3,
        completed=3,
        results=[
            _item(1, "OpenAI", "gpt-a", "healthy"),
            _item(1, "OpenAI", "gpt-b", "timeout", error="超时"),
            _item(2, "Claude", "claude-x", "healthy"),
        ],
    )

    out = await provider_patrol.patrol_status(_ctx(), {"run_id": "run-1", "only_failed": True})
    assert [g["provider_id"] for g in out["providers"]] == [1]
    assert [m["model"] for m in out["providers"][0]["models"]] == ["gpt-b"]

    limited = await provider_patrol.patrol_status(_ctx(), {"run_id": "run-1", "limit": 1})
    assert sum(len(g["models"]) for g in limited["providers"]) == 1
    assert limited["models_omitted"] == 2


@pytest.mark.asyncio
async def test_patrol_status_running_and_unknown_run(fake_api) -> None:
    running = await provider_patrol.patrol_status(_ctx(), {"run_id": "run-1"})
    assert running["finished"] is False and "不要重新发起" in running["note"]

    missing = await provider_patrol.patrol_status(_ctx(), {"run_id": "gone"})
    assert missing["found"] is False and "过期" in missing["note"]


@pytest.mark.asyncio
async def test_patrol_status_defaults_to_latest_executed_action(fake_api) -> None:
    row = SimpleNamespace(result={"run_id": "run-1"})
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: row))

    out = await provider_patrol.patrol_status(_ctx(db, session=SimpleNamespace(id="s1")), {})

    assert out["run_id"] == "run-1"
    assert fake_api["status_calls"] == ["run-1"]


@pytest.mark.asyncio
async def test_patrol_status_without_any_run(fake_api) -> None:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))
    out = await provider_patrol.patrol_status(_ctx(db), {})
    assert out["found"] is False
    assert fake_api["status_calls"] == []


@pytest.mark.asyncio
async def test_patrol_cancel_preview_and_execute(fake_api) -> None:
    prepared = await provider_patrol.cancel_preview(_ctx(), {"run_id": "run-1"})
    assert prepared.arguments == {"run_id": "run-1"}
    assert fake_api["cancel_calls"] == []  # 预览不取消

    result = await provider_patrol.cancel_execute(_ctx(), {"run_id": "run-1"})
    assert result["cancel_requested"] is True
    assert fake_api["cancel_calls"] == ["run-1"]

    fake_api["snapshot"] = FullLivenessRunResponse(run_id="run-1", status="completed", task_total=3, completed=3)
    with pytest.raises(ValueError, match="已结束"):
        await provider_patrol.cancel_preview(_ctx(), {"run_id": "run-1"})


@pytest.mark.parametrize(
    "text",
    (
        "巡检一下全部模型能不能用",
        "帮我测活所有 provider",
        "现在模型能不能用",
        "全部模型跑一遍测活",
    ),
)
def test_patrol_questions_route_to_provider_skill(text: str) -> None:
    reset_registry_for_tests()
    specs = get_registry().list_all()
    available = {tool_domain(spec) for spec in specs}
    route = route_locally(text, available=available)

    assert route is not None
    assert "providers" in route.domains
    skills = SkillRegistry(BUILTIN_SKILLS)
    selected = skills.select(route)
    narrowed = skills.narrow_tools(select_tool_specs(specs, route), selected)
    names = {spec.name for spec in narrowed}
    assert "providers.patrol" in names
    assert "providers.patrol_status" in names


def test_ai_config_skill_lists_patrol_tools() -> None:
    skill = next(s for s in BUILTIN_SKILLS if s.name == "ai-config")
    for name in ("providers.patrol", "providers.patrol_status", "providers.patrol_cancel"):
        assert name in skill.allowed_tools
    assert any("不得自动重新发起" in line for line in skill.instructions)
