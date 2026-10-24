"""System Agent 一键体检：diagnostics.system_checkup / diagnostics.account_checkup。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import ARRAY as GenericArray
from sqlalchemy.dialects.postgresql import ARRAY as PgArray
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.system_agent.context import ToolContext
from app.services.system_agent.registry import get_registry, reset_registry_for_tests
from app.services.system_agent.skills.catalog import BUILTIN_SKILLS
from app.services.system_agent.skills.registry import SkillRegistry
from app.services.system_agent.tool_routing import (
    domain_catalog,
    route_locally,
    select_tool_specs,
    tool_domain,
)
from app.services.system_agent.tools import diagnostics as diag

SECRET = "sk-abcdefghijklmnopqrstuvwxyz123456"


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def _ctx(channel: str = "web", role: str = "viewer", account_id: int | None = None) -> ToolContext:
    # 无 begin_nested 的假 session：_savepoint 直接放行
    return ToolContext(db=SimpleNamespace(), channel=channel, role=role, account_id=account_id)


def _opts() -> diag.CheckOptions:
    now = datetime.now(UTC)
    return diag.CheckOptions(now=now, hours=24, since=now - timedelta(hours=24))


# ── 注册 / 渠道 / 角色 ────────────────────────────────────────────


def test_checkup_tools_registered_read_only_for_web_and_bot_viewers() -> None:
    registry = get_registry()
    for name in ("diagnostics.system_checkup", "diagnostics.account_checkup"):
        spec = registry.get(name)
        assert spec is not None
        assert spec.read_only is True
        assert spec.min_role == "viewer"
        assert set(spec.channels) == {"web", "bot"}
        assert spec.read_handler is not None
        assert tool_domain(spec) == "diagnostics"
    for channel in ("web", "bot"):
        names = {s.name for s in registry.list_for(channel=channel, role="viewer")}
        assert {"diagnostics.system_checkup", "diagnostics.account_checkup"} <= names


def test_checkup_skill_only_references_registered_tools() -> None:
    skill = next(s for s in BUILTIN_SKILLS if s.name == "checkup")
    registered = {spec.name for spec in get_registry().list_all()}
    assert skill.domains == ("diagnostics",)
    assert set(skill.allowed_tools) == {"diagnostics.system_checkup", "diagnostics.account_checkup"}
    assert set(skill.allowed_tools) <= registered
    assert "diagnostics" in domain_catalog()


# ── 路由 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "体检",
        "一键体检",
        "帮我做个健康检查",
        "全面检查一下",
        "系统整体有没有问题",
        "账号 A 为什么不自动回复了",
        "这个账号没反应",
        "账号 3 不工作了",
        "客服号不回复了怎么回事",
        "run a checkup",
    ],
)
def test_checkup_phrases_route_to_diagnostics_first(text: str) -> None:
    route = route_locally(text, available=set(domain_catalog()))
    assert route is not None
    assert route.domains[0] == "diagnostics"
    assert len(route.domains) <= 3


def test_checkup_tool_alone_is_disclosed_with_single_domain() -> None:
    registry = get_registry()
    specs = registry.list_for(channel="web", role="viewer")
    route = route_locally("系统整体有没有问题", available={tool_domain(s) for s in specs})
    assert route is not None and route.domains == ("diagnostics",)
    skills = SkillRegistry(BUILTIN_SKILLS)
    selected = skills.select(route)
    assert [s.name for s in selected] == ["checkup"]
    names = [s.name for s in skills.narrow_tools(select_tool_specs(specs, route), selected)]
    assert names == ["diagnostics.system_checkup", "diagnostics.account_checkup"]


def test_account_checkup_keeps_accounts_follow_up_tools() -> None:
    registry = get_registry()
    specs = registry.list_for(channel="web", role="admin")
    route = route_locally("账号 A 为什么不自动回复了", available={tool_domain(s) for s in specs})
    assert route is not None and route.domains[:2] == ("diagnostics", "accounts")
    skills = SkillRegistry(BUILTIN_SKILLS)
    selected = skills.select(route)
    assert [s.name for s in selected][:2] == ["checkup", "accounts"]
    names = {s.name for s in skills.narrow_tools(select_tool_specs(specs, route), selected)}
    assert {"diagnostics.account_checkup", "accounts.restart_worker"} <= names


def test_weak_or_specific_requests_keep_existing_routes() -> None:
    available = set(domain_catalog())
    model_health = route_locally("模型健康检查", available=available)
    assert model_health is not None and model_health.domains[0] == "providers"
    plugin_debug = route_locally("插件 lottery 不工作了，帮我排查", available=available)
    assert plugin_debug is not None and plugin_debug.domains[0] == "plugins"
    assert "diagnostics" not in plugin_debug.domains
    restart = route_locally("重启账号 3 的 worker", available=available)
    assert restart is not None and restart.domains == ("accounts",)
    no_diag = route_locally("一键体检", available=available - {"diagnostics"})
    assert no_diag is None or "diagnostics" not in no_diag.domains


# ── 检查段降级 ────────────────────────────────────────────────────


def _plan_names() -> list[str]:
    return [fn_name for _key, _title, fn_name, _sp in diag.SYSTEM_PLAN]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", [fn for _k, _t, fn, _s in diag.SYSTEM_PLAN])
async def test_each_system_section_degrades_to_unknown(monkeypatch, failing: str) -> None:
    async def ok(ctx, opts):
        return diag._section(diag.STATUS_OK, "正常。")

    async def boom(ctx, opts):
        raise RuntimeError(f"connection refused api_key={SECRET}\nSELECT secret FROM table")

    for name in _plan_names():
        monkeypatch.setattr(diag, name, boom if name == failing else ok)

    result = await diag.system_checkup(_ctx(), {})

    assert len(result["sections"]) == len(diag.SYSTEM_PLAN)
    by_fn = {fn: section for (_k, _t, fn, _s), section in zip(diag.SYSTEM_PLAN, result["sections"], strict=True)}
    failed = by_fn[failing]
    assert failed["status"] == "unknown"
    assert SECRET not in failed["reason"]
    assert "SELECT" not in failed["reason"]
    assert failed["reason"].startswith("RuntimeError")
    assert all(s["status"] == "ok" for fn, s in by_fn.items() if fn != failing)
    assert result["verdict"] == "ok"
    assert result["unknown_sections"] == [failed["key"]]
    assert result["business_changed"] is False


@pytest.mark.asyncio
async def test_section_timeout_and_total_budget(monkeypatch) -> None:
    async def slow(ctx, opts):
        await asyncio.sleep(5)
        return diag._section(diag.STATUS_OK, "不该到这里")

    async def fast(ctx, opts):
        return diag._section(diag.STATUS_WARN, "需关注。", next_steps=[diag._step("看看", tool="logs.search_errors")])

    monkeypatch.setattr(diag, "_check_health", slow)
    monkeypatch.setattr(diag, "_check_versions", fast)
    plan = (
        ("health", "基础设施", "_check_health", False),
        ("versions", "版本", "_check_versions", False),
        ("health2", "基础设施2", "_check_health", False),
    )
    sections = await diag.run_sections(_ctx(), _opts(), plan, section_timeout=0.1, total_budget=0.3)
    assert sections[0]["status"] == "unknown" and sections[0]["reason"] == "timeout"
    assert sections[1]["status"] == "warn"
    assert sections[2]["status"] == "unknown"
    assert sections[2]["reason"] in {"timeout", "total_budget_exceeded"}


def test_summarize_orders_next_steps_and_verdict() -> None:
    sections = [
        {"key": "a", "status": "warn", "next_steps": [diag._step("警告步骤", tool="x")]},
        {"key": "b", "status": "error", "next_steps": [diag._step("严重步骤", severity="error", tool="y")]},
        {"key": "c", "status": "unknown", "next_steps": []},
        {"key": "d", "status": "warn", "next_steps": [diag._step("警告步骤", tool="x")]},
    ]
    summary = diag.summarize_sections(sections)
    assert summary["verdict"] == "error"
    assert summary["counts"] == {"ok": 0, "warn": 2, "error": 1, "unknown": 1}
    assert [s["text"] for s in summary["next_steps"]] == ["严重步骤", "警告步骤"]
    assert [s["order"] for s in summary["next_steps"]] == [1, 2]
    assert summary["unknown_sections"] == ["c"]
    assert diag.summarize_sections([{"key": "x", "status": "unknown"}])["verdict"] == "unknown"


# ── 账号解析 ──────────────────────────────────────────────────────


def _rows():
    return [
        SimpleNamespace(id=1, display_name="客服A", tg_username="alice", phone="8613800001234", status="active"),
        SimpleNamespace(id=2, display_name="客服B", tg_username="bob", phone="8613900005678", status="paused"),
        SimpleNamespace(id=12, display_name="运营", tg_username="ops12", phone="447700900111", status="active"),
    ]


def test_match_accounts_by_id_username_name_and_phone() -> None:
    rows = _rows()
    assert [r.id for r in diag.match_accounts(rows, "2")] == [2]
    assert [r.id for r in diag.match_accounts(rows, "#12")] == [12]
    assert [r.id for r in diag.match_accounts(rows, "@Alice")] == [1]
    assert [r.id for r in diag.match_accounts(rows, "客服B")] == [2]
    assert [r.id for r in diag.match_accounts(rows, "5678")] == [2]
    assert [r.id for r in diag.match_accounts(rows, "+86 139 0000 5678")] == [2]
    assert [r.id for r in diag.match_accounts(rows, "客服")] == [1, 2]
    assert diag.match_accounts(rows, "不存在") == []


@pytest.mark.asyncio
async def test_resolve_account_web_variants(monkeypatch) -> None:
    async def fake_rows(ctx):
        return _rows()

    monkeypatch.setattr(diag, "_load_account_rows", fake_rows)
    assert await diag.resolve_account(_ctx(), {"account_id": 5}) == (5, None)
    assert await diag.resolve_account(_ctx(), {"account": "@bob"}) == (2, None)

    aid, err = await diag.resolve_account(_ctx(), {"account": "客服"})
    assert aid is None and err["error"] == "ambiguous"
    assert [c["id"] for c in err["candidates"]] == [1, 2]
    assert all("1234" not in str(c["phone"])[:-4] for c in err["candidates"])
    assert err["candidates"][0]["phone"].endswith("1234") and "*" in err["candidates"][0]["phone"]

    aid, err = await diag.resolve_account(_ctx(), {"account": "nobody"})
    assert aid is None and err["error"] == "not_found"

    aid, err = await diag.resolve_account(_ctx(), {})
    assert aid is None and err["error"] == "account_required"
    assert await diag.resolve_account(_ctx(account_id=12), {}) == (12, None)


@pytest.mark.asyncio
async def test_resolve_account_bot_is_bound_to_context(monkeypatch) -> None:
    async def fake_rows(ctx):
        return _rows()

    monkeypatch.setattr(diag, "_load_account_rows", fake_rows)
    assert await diag.resolve_account(_ctx("bot", account_id=1), {}) == (1, None)
    assert await diag.resolve_account(_ctx("bot", account_id=1), {"account": "alice"}) == (1, None)
    aid, err = await diag.resolve_account(_ctx("bot", account_id=1), {"account_id": 2})
    assert aid is None and err["error"] == "forbidden"
    aid, err = await diag.resolve_account(_ctx("bot", account_id=1), {"account": "@bob"})
    assert aid is None and err["error"] == "forbidden"


# ── 原因推断 ──────────────────────────────────────────────────────


def _healthy_facts() -> dict:
    return {
        "unavailable": {},
        "account": {"id": 1, "status": "active", "session_decryptable": True},
        "kill_switch": False,
        "worker": {"observed": True, "alive": True, "fail_count": 0},
        "features": {"enabled": [{"feature_key": "auto_reply"}], "enabled_but_package_disabled": [], "failing": []},
        "rules": {"interaction_rules_enabled": 0, "interaction_module_enabled": True, "ai_commands": []},
        "providers": {"providers": [], "missing_provider_ids": []},
        "activity": {"window_hours": 24, "errors": {"error_count": 0}, "last_activity_at": "2026-10-25T00:00:00+00:00"},
        "humanize": {"in_active_window": True},
    }


def test_healthy_account_has_no_causes() -> None:
    assert diag.diagnose_account_causes(_healthy_facts()) == []


def test_causes_cover_common_reasons_and_are_ordered() -> None:
    facts = _healthy_facts()
    facts["humanize"] = {"in_active_window": False, "active_window_start": "09:00", "active_window_end": "18:00"}
    facts["worker"] = {"observed": False, "alive": False, "fail_count": 3}
    facts["features"]["enabled_but_package_disabled"] = ["auto_reply"]
    facts["rules"]["interaction_rules_enabled"] = 2
    facts["rules"]["interaction_module_enabled"] = False
    facts["providers"] = {
        "providers": [
            {"name": "OpenAI", "status": "error", "has_api_key": False, "issues": ["缺少 API Key"], "used_by": ["command:ask"]}
        ],
        "missing_provider_ids": [99],
    }
    causes = diag.diagnose_account_causes(facts)
    codes = [c["code"] for c in causes]
    for code in (
        "worker_down",
        "package_disabled",
        "interaction_module_paused",
        "provider_unhealthy",
        "provider_missing",
        "outside_active_window",
    ):
        assert code in codes
    ranks = [diag._CONFIDENCE_RANK[c["confidence"]] for c in causes]
    assert ranks == sorted(ranks)
    worker = next(c for c in causes if c["code"] == "worker_down")
    assert worker["tool"] == "accounts.restart_worker" and worker["requires_confirmation"] is True
    assert "worker.fail_count=3" in worker["evidence"]


def test_paused_and_kill_switch_take_priority_and_hide_worker_noise() -> None:
    facts = _healthy_facts()
    facts["kill_switch"] = True
    facts["account"]["status"] = "paused"
    facts["worker"] = {"observed": False, "alive": False}
    codes = [c["code"] for c in diag.diagnose_account_causes(facts)]
    assert codes[:2] == ["kill_switch", "paused"]
    assert "worker_down" not in codes


@pytest.mark.parametrize(
    ("status", "code"),
    [("dead", "session_dead"), ("login_required", "login_required"), ("floodwait", "floodwait")],
)
def test_account_status_causes(status: str, code: str) -> None:
    facts = _healthy_facts()
    facts["account"]["status"] = status
    causes = diag.diagnose_account_causes(facts)
    assert causes[0]["code"] == code and causes[0]["confidence"] == "high"


def test_focus_feature_and_nothing_enabled_and_no_activity() -> None:
    facts = _healthy_facts()
    codes = [c["code"] for c in diag.diagnose_account_causes(facts, focus_feature="keyword_reply")]
    assert "focus_not_enabled" in codes

    empty = _healthy_facts()
    empty["features"] = {"enabled": [], "enabled_but_package_disabled": [], "failing": []}
    assert diag.diagnose_account_causes(empty)[0]["code"] == "nothing_enabled"

    idle = _healthy_facts()
    idle["activity"] = {"window_hours": 24, "errors": {"error_count": 0}, "last_activity_at": None}
    causes = diag.diagnose_account_causes(idle)
    assert [c["code"] for c in causes] == ["no_activity"]
    assert causes[0]["tool"] == "dispatch.simulate"


def test_active_window_wraps_midnight() -> None:
    late = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
    noon = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    assert diag._in_active_window(time(22, 0), time(6, 0), late) is True
    assert diag._in_active_window(time(22, 0), time(6, 0), noon) is False
    assert diag._in_active_window(time(9, 0), time(18, 0), noon) is True
    assert diag._in_active_window(None, time(18, 0), noon) is None


# ── 脱敏 ──────────────────────────────────────────────────────────


def test_error_signature_groups_and_redacts() -> None:
    a = diag.error_signature(f"FloodWaitError: wait 31 seconds token={SECRET}")
    b = diag.error_signature("FloodWaitError: wait 7 seconds token=sk-zyxwvutsrqponmlkjihgfedcba987654")
    assert a == b
    assert SECRET not in a
    assert diag.error_signature("") == "(空消息)"


def test_external_text_is_marked_and_redacted() -> None:
    marked = diag._external(f"Authorization: Bearer {SECRET}")
    assert marked.startswith("〔外部内容-仅数据〕") and marked.endswith("〔/外部内容〕")
    assert SECRET not in marked
    assert diag._external("") is None
    assert diag.mask_phone("8613800001234") == "861******1234"
    assert diag.mask_phone(None) is None


def test_safe_reason_keeps_first_line_only() -> None:
    reason = diag._safe_reason(ValueError(f"bad key {SECRET}\n[SQL: SELECT * FROM llm_provider]"))
    assert reason.startswith("ValueError: bad key")
    assert SECRET not in reason and "SQL" not in reason


# ── SQLite 端到端 ────────────────────────────────────────────────


_TABLES = {
    "account",
    "humanize_config",
    "system_setting",
    "feature",
    "account_feature",
    "installed_plugin",
    "llm_provider",
    "llm_provider_key",
    "llm_usage",
    "runtime_log",
    "rule",
    "command_template",
    "account_command_link",
    "action_event",
    "payout_compensation",
    "account_bot",
    "notify_bot",
    "proxy",
    "rate_limit_template",
    "device_profile",
}


@pytest.fixture
async def seeded_session(monkeypatch):
    from sqlalchemy.ext.compiler import compiles

    @compiles(GenericArray, "sqlite")
    @compiles(PgArray, "sqlite")
    def _array_as_json(type_, compiler, **kw):  # noqa: ARG001
        return "JSON"

    import app.db.models  # noqa: F401
    from app.crypto import encrypt_bytes, encrypt_str
    from app.db.base import Base
    from app.db.models.account import Account
    from app.db.models.command import LLMProvider
    from app.db.models.feature import AccountFeature, Feature
    from app.db.models.llm_usage import LLMUsage
    from app.db.models.log import RuntimeLog

    class _FakeRedis:
        async def ping(self):
            return True

    from app.services.system_agent.tools import system as system_tools

    monkeypatch.setattr(system_tools, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(
        diag,
        "_worker_snapshot",
        lambda: {3: {"account_id": 3, "alive": True, "desired": "running", "fail_count": 0}},
    )

    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [table for name, table in Base.metadata.tables.items() if name in _TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    async with AsyncSession(engine) as db:
        good = {"api_id_enc": encrypt_str("1"), "api_hash_enc": encrypt_str("h"), "session_enc": encrypt_bytes(b"s")}
        db.add_all(
            [
                Account(id=1, phone="8613800001234", display_name="客服A", tg_username="alice", status="active", **good),
                Account(id=2, phone="8613900005678", display_name="客服B", tg_username="bob", status="paused",
                        api_id_enc="bad", api_hash_enc="bad", session_enc=b"bad"),
                Account(id=3, phone="447700900111", display_name="运营", tg_username="ops", status="active", **good),
            ]
        )
        db.add(Feature(key="auto_reply", display_name="自动回复", is_builtin=False))
        db.add(AccountFeature(account_id=1, feature_key="auto_reply", enabled=True, config={"provider_id": 9},
                              last_error=f"boom token={SECRET}"))
        db.add(AccountFeature(account_id=3, feature_key="auto_reply", enabled=True, config={}))
        db.add(LLMProvider(id=9, name="OpenAI", provider="openai", base_url="https://api.example.com",
                           default_model="gpt", api_key_enc=None, models=[{"id": "gpt", "enabled": True}]))
        now = datetime.now(UTC)
        for i in range(30):
            db.add(RuntimeLog(id=i + 1, account_id=1, ts=now - timedelta(minutes=i), level="ERROR", source="worker",
                              message=f"FloodWaitError: wait {i} seconds api_key={SECRET}"))
        db.add(LLMUsage(id=1, account_id=3, provider_id=9, model="gpt", success=False, error_type="auth_failed",
                        input_tokens=0, output_tokens=0, latency_ms=10, used_fallback=False,
                        created_at=now - timedelta(minutes=5)))
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_system_checkup_end_to_end_on_sqlite(seeded_session) -> None:
    result = await diag.system_checkup(ToolContext(db=seeded_session, channel="web", role="viewer"), {"hours": 24})
    sections = {s["key"]: s for s in result["sections"]}
    assert set(sections) == {k for k, _t, _f, _s in diag.SYSTEM_PLAN}
    assert sections["accounts"]["status"] == "error"
    assert [p["id"] for p in sections["accounts"]["facts"]["problem_accounts"]] == [1, 2]
    assert sections["errors"]["facts"]["error_count"] == 30
    assert sections["errors"]["facts"]["top"][0]["count"] == 30
    assert sections["providers"]["status"] == "error"
    provider = sections["providers"]["facts"]["providers"][0]
    assert "缺少 API Key" in provider["issues"]
    assert provider["recent_error_types"] == {"auth_failed": 1}
    assert "base_url" not in provider
    assert sections["plugins"]["facts"]["account_feature_failures"][0]["feature_key"] == "auto_reply"
    assert result["verdict"] == "error"
    assert result["next_steps"][0]["severity"] == "error"
    assert any(step["tool"] == "accounts.restart_worker" for step in result["next_steps"])
    assert SECRET not in repr(result)
    assert all(s["status"] != "unknown" or s["key"] == "health" for s in result["sections"]), result["unknown_sections"]


@pytest.mark.asyncio
async def test_system_checkup_bot_channel_is_scoped(seeded_session) -> None:
    result = await diag.system_checkup(ToolContext(db=seeded_session, channel="bot", role="viewer", account_id=3), {})
    sections = {s["key"]: s for s in result["sections"]}
    assert result["scope"]["account_id"] == 3
    assert sections["accounts"]["facts"]["total"] == 1
    assert sections["accounts"]["status"] == "ok"
    assert sections["errors"]["facts"]["error_count"] == 0


@pytest.mark.asyncio
async def test_account_checkup_end_to_end_on_sqlite(seeded_session) -> None:
    ctx = ToolContext(db=seeded_session, channel="web", role="viewer")
    result = await diag.account_checkup(ctx, {"account": "客服A", "feature_key": "auto_reply"})
    assert result["account_id"] == 1
    assert result["verdict"] == "error"
    codes = [c["code"] for c in result["likely_causes"]]
    assert codes[0] == "worker_down"
    assert {"feature_error", "provider_unhealthy", "recent_errors", "floodwait_logs"} <= set(codes)
    assert result["facts"]["unavailable"] == {}
    assert result["facts"]["account"]["phone"] == "861******1234"
    assert SECRET not in repr(result)

    paused = await diag.account_checkup(ctx, {"account": "5678"})
    assert paused["account_id"] == 2
    assert [c["code"] for c in paused["likely_causes"]][:2] == ["paused", "secrets_undecryptable"]

    ambiguous = await diag.account_checkup(ctx, {"account": "客服"})
    assert ambiguous["error"] == "ambiguous"

    provider_issue = await diag.account_checkup(ctx, {"account_id": 3})
    assert provider_issue["likely_causes"][0]["code"] == "provider_unhealthy"
    assert provider_issue["likely_causes"][0]["confidence"] == "high"


@pytest.mark.asyncio
async def test_total_budget_exhausted_skips_remaining_sections(monkeypatch) -> None:
    async def slow(ctx, opts):
        await asyncio.sleep(5)

    monkeypatch.setattr(diag, "_check_health", slow)
    plan = (
        ("health", "基础设施", "_check_health", False),
        ("versions", "版本", "_check_versions", False),
    )
    sections = await diag.run_sections(_ctx(), _opts(), plan, section_timeout=1.0, total_budget=0.1)
    assert sections[0]["reason"] == "timeout"
    assert sections[1]["status"] == "unknown"
    assert sections[1]["reason"] == "total_budget_exceeded"
