"""插件平台化 P3：控件契约、卡片 DSL、配置钩子与 x-secret-list。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import plugin_compat
from app.api import features as features_api
from app.services import plugin_config_hooks, plugin_secret_list, plugin_ui_cards
from app.worker.plugins import capabilities as caps_mod
from app.worker.plugins import plugin_stats, plugin_ui
from app.worker.plugins.base import Plugin, PluginContext
from app.worker.plugins.versioning import PLUGIN_API_VERSION

REPO = Path(__file__).resolve().parents[3]


from app.tests.test_plugin_platform_p0 import _FakeRedis  # noqa: E402 - 与 P0 统计测试共用 Redis 替身


@pytest.fixture(autouse=True)
def _reset_stats():
    recorder = plugin_stats.get_recorder()
    recorder.reset_local()
    recorder.set_redis_factory(None)
    yield
    recorder.reset_local()


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    redis = _FakeRedis()
    import app.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    return redis


# ─────────────────────────────────────────────────────
# 契约
# ─────────────────────────────────────────────────────


def test_contract_copies_match_source() -> None:
    source = (REPO / "schemas/plugin-ui-widgets.json").read_text(encoding="utf-8")
    for copy in (
        REPO / "backend/app/worker/plugins/plugin_ui_widgets.json",
        REPO / "frontend/src/lib/pluginUiWidgets.json",
        REPO / "ios/TelePilotCore/Sources/TelePilotCore/plugin-ui-widgets.json",
    ):
        assert copy.read_text(encoding="utf-8") == source, copy


def test_contract_lists_ios_first_batch_widgets_and_five_layouts() -> None:
    ids = plugin_ui.widget_ids()
    for widget in ("config-list", "allowed-peer-multi-select", "multi-select", "list-select", "secret-list"):
        assert widget in ids
    assert sorted(plugin_ui.card_layouts()) == ["buttons", "kv", "list", "stats", "table"]
    assert plugin_ui.card_slots()[:3] == ["plugin.overview", "account.detail", "dashboard"]
    assert "interaction.rule_editor" in plugin_ui.card_slots()
    assert plugin_ui.hook_timeout_seconds("on_ui_card") == 3
    assert plugin_ui.hook_timeout_seconds("on_config_validate") == 2


def test_unknown_widgets_are_linted() -> None:
    schema = {
        "properties": {
            "a": {"type": "string", "x-ui-widget": "llm-model-select"},
            "b": {"type": "string", "x-ui-widget": "color-wheel"},
            "c": {"type": "array", "items": {"type": "object", "properties": {"d": {"type": "string", "x-ui-widget": "map"}}}},
        }
    }
    assert plugin_ui.unknown_widgets_in_schema(schema) == ["b: color-wheel", "c[].d: map"]


def test_p3_capabilities_and_plugin_api() -> None:
    assert tuple(int(x) for x in PLUGIN_API_VERSION.split(".")) >= (1, 3)
    for name in ("ui.cards", "config.validate", "config.saved", "config.secret_list", "ui.widgets"):
        item = caps_mod.CAPABILITY_INDEX[name]
        assert item.since == caps_mod._THIS_RELEASE
        assert item.plugin_api == "1.3"
    ctx = PluginContext(account_id=1, feature_key="demo")
    assert ctx.capabilities.has("ui.cards")


# ─────────────────────────────────────────────────────
# 卡片声明
# ─────────────────────────────────────────────────────


def test_normalize_ui_cards_accepts_valid_and_reports_invalid() -> None:
    ui = {
        "cards": [
            {
                "id": "health",
                "slot": "plugin.overview",
                "title": {"zh": "最近 24 小时", "en": "Last 24h"},
                "source": {"read": "plugin.stats@1"},
                "layout": {"type": "stats", "items": [{"label": "调用", "path": "totals.calls"}]},
            },
            {"id": "quota", "slot": "account.detail", "title": "配额", "source": {"action": "quota"}, "blocks": [{"type": "kv"}, {"type": "buttons", "items": [{"id": "refresh", "label": "刷新"}]}], "refresh_seconds": 5},
            {"id": "bad-slot", "slot": "sidebar", "source": {"action": "x"}, "layout": {"type": "stats"}},
            {"id": "undeclared", "slot": "dashboard", "source": {"read": "chat.allowed@1"}, "layout": {"type": "list"}},
            {"id": "chart", "slot": "dashboard", "source": {"action": "x"}, "layout": {"type": "chart"}},
            {"id": "_host.fake", "slot": "dashboard", "source": {"action": "x"}, "layout": {"type": "kv"}},
        ]
    }
    cards, warnings = plugin_ui.normalize_ui_cards(ui, reads=["plugin.stats@1"])
    assert [card["id"] for card in cards] == ["health", "quota"]
    assert cards[0]["title"] == "最近 24 小时"
    assert cards[0]["blocks"][0]["type"] == "stats"
    assert cards[1]["refresh_seconds"] == 30
    assert len(warnings) == 4
    assert any("sidebar" in item for item in warnings)
    assert any("没有在 plugin.json reads 里声明" in item for item in warnings)
    assert any("chart" in item for item in warnings)


def test_lint_ui_declaration_combines_cards_and_widgets() -> None:
    warnings = plugin_ui.lint_ui_declaration(
        {"ui": {"cards": [{"id": "x", "slot": "nope"}]}, "config_schema": {"properties": {"a": {"x-ui-widget": "rainbow"}}}}
    )
    assert any("nope" in item for item in warnings)
    assert any("rainbow" in item for item in warnings)


# ─────────────────────────────────────────────────────
# x-secret-list
# ─────────────────────────────────────────────────────

SECRET_SCHEMA = {
    "properties": {
        "tokens": {
            "type": "array",
            "x-secret-list": {"secret_field": "token", "note_field": "note"},
            "items": {"type": "object", "properties": {"token": {"type": "string"}, "note": {"type": "string"}}},
        },
        "command": {"type": "string"},
    }
}


def test_secret_list_sanitize_and_preserve_roundtrip() -> None:
    long_token = "eyJ" + "a" * 40 + "tail"
    stored = {"tokens": [{"token": long_token, "note": "主号"}, {"token": "short-token-1234", "note": ""}], "command": "img"}
    sanitized = features_api._sanitize_config(stored, "demo_image", SECRET_SCHEMA)  # noqa: SLF001
    assert sanitized["command"] == "img"
    first, second = sanitized["tokens"]
    assert long_token not in json.dumps(sanitized)
    assert first["token"] == f"{long_token[:10]}···{long_token[-10:]}"
    assert first["secret_id"].startswith("token:") and first["note"] == "主号"
    assert second["token"] == "shor···1234"

    # 客户端：改第一项备注、删第二项、按遮罩回传第三项不存在 → 新增明文
    incoming = {
        "tokens": [
            {"token": first["token"], "note": "主号-改", "secret_id": first["secret_id"]},
            {"token": "brand-new-token", "note": "新"},
        ],
        "command": "img",
    }
    saved = features_api._preserve_existing_sensitive_values(stored, incoming, "demo_image", SECRET_SCHEMA)  # noqa: SLF001
    assert saved["tokens"] == [{"token": long_token, "note": "主号-改"}, {"token": "brand-new-token", "note": "新"}]


def test_chatgpt_image_legacy_compat_matches_old_behaviour() -> None:
    specs = plugin_compat.legacy_secret_list_fields("chatgpt_image", {"properties": {"tokens": {"type": "array"}}})
    assert specs["tokens"]["id_field"] == "token_id" and specs["tokens"]["legacy_field"] == "token"
    # 新版本声明了 x-secret-list：不再套兼容表
    assert plugin_compat.legacy_secret_list_fields("chatgpt_image", SECRET_SCHEMA) == {}
    sanitized = features_api._sanitize_chatgpt_image_config({"token": "legacy-a,legacy-b", "tokens": []})  # noqa: SLF001
    assert [item["token"] for item in sanitized["tokens"]] == ["lega···cy-a", "lega···cy-b"]
    assert sanitized["token"] == ""
    assert all(item["token_id"].startswith("token:") for item in sanitized["tokens"])
    saved = features_api._preserve_existing_sensitive_values(  # noqa: SLF001
        {"token": "legacy-a"},
        {"tokens": [{"token": "lega···cy-a", "token_id": sanitized["tokens"][0]["token_id"]}]},
        "chatgpt_image",
    )
    assert saved["tokens"] == [{"token": "legacy-a", "note": ""}]
    assert saved["token"] == ""


def test_secret_spec_infers_secret_field() -> None:
    field = {"type": "array", "x-secret-list": True, "items": {"properties": {"api_key": {}, "note": {}}}}
    assert plugin_secret_list.normalize_spec(True, field)["secret_field"] == "api_key"


# ─────────────────────────────────────────────────────
# 配置钩子
# ─────────────────────────────────────────────────────


class _ValidatingPlugin(Plugin):
    key = "validating"
    display_name = "Validating"
    saved: list[tuple[dict, str]] = []

    async def on_config_validate(self, ctx: PluginContext, config: dict, scope: str) -> dict | None:
        if config.get("mode") == "bad":
            return {"errors": {"mode": "不支持这个模式"}}
        if config.get("model") == "gpt-5.4":
            return {"config": {**config, "model": "gpt-5.5"}}
        if config.get("mode") == "slow":
            await asyncio.sleep(5)
        if config.get("mode") == "boom":
            raise RuntimeError("插件内部错误")
        return None

    async def on_config_saved(self, ctx: PluginContext, config: dict, scope: str) -> None:
        type(self).saved.append((dict(config), scope))


class _PlainPlugin(Plugin):
    key = "codex_image"
    display_name = "Plain"


def _feature(key: str = "validating") -> SimpleNamespace:
    return SimpleNamespace(key=key, manifest={"permissions": []})


async def _fake_ctx(*_args: Any, **kwargs: Any) -> PluginContext:
    return PluginContext(account_id=7, feature_key=kwargs["feature"].key, config=dict(kwargs["config"]))


@pytest.fixture
def hook_env(monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis):
    from app.services import plugin_config_actions

    classes: dict[str, type[Plugin]] = {"validating": _ValidatingPlugin, "codex_image": _PlainPlugin}

    async def _load(db, key, account_id, *, installed_plugin=None):  # noqa: ANN001, ANN202, ARG001
        return classes[key]

    class _DB:
        async def get(self, model, key):  # noqa: ANN001, ANN202, ARG002
            return None

    monkeypatch.setattr(plugin_config_actions, "load_plugin_class", _load)
    monkeypatch.setattr(plugin_config_actions, "build_main_process_context", _fake_ctx)
    _ValidatingPlugin.saved = []
    return _DB()


async def test_config_validate_rewrites_rejects_and_fails_open(hook_env, monkeypatch: pytest.MonkeyPatch) -> None:
    db = hook_env
    account = SimpleNamespace(id=7)
    ok = await plugin_config_hooks.run_config_validate(db, feature=_feature(), account=account, config={"model": "gpt-5.4"}, scope="account")
    assert ok.handled and ok.config == {"model": "gpt-5.5"} and not ok.errors

    bad = await plugin_config_hooks.run_config_validate(db, feature=_feature(), account=account, config={"mode": "bad"}, scope="account")
    assert bad.errors == {"mode": "不支持这个模式"}
    assert plugin_config_hooks.format_validate_errors(bad.errors) == "配置验证失败: mode: 不支持这个模式"

    monkeypatch.setattr(plugin_config_hooks, "hook_timeout_seconds", lambda hook: 0.05)
    slow = await plugin_config_hooks.run_config_validate(db, feature=_feature(), account=account, config={"mode": "slow"}, scope="account")
    assert slow.failed == "timeout" and slow.config == {"mode": "slow"} and not slow.errors
    boom = await plugin_config_hooks.run_config_validate(db, feature=_feature(), account=None, config={"mode": "boom"}, scope="global")
    assert boom.failed == "RuntimeError" and not boom.errors

    stats = await plugin_stats.read_plugin_stats(plugin_config_hooks_redis(), "validating")
    outcomes = {row["event_type"]: row for row in stats["hooks"] if row["hook"] == "on_config_validate"}
    assert outcomes["account"]["timeout"] >= 1
    assert outcomes["global"]["failed"] >= 1


def plugin_config_hooks_redis() -> Any:
    import app.redis_client as redis_client

    return redis_client.get_redis()


async def test_plugin_without_validate_keeps_legacy_rename(hook_env) -> None:
    outcome = await plugin_config_hooks.run_config_validate(
        hook_env, feature=_feature("codex_image"), account=None, config={"model": "gpt-5.4"}, scope="global"
    )
    assert not outcome.handled
    assert outcome.config == {"model": "gpt-5.5"}


async def test_config_saved_runs_in_background(hook_env) -> None:
    task = await plugin_config_hooks.schedule_config_saved(
        hook_env, feature=_feature(), account=SimpleNamespace(id=7), config={"a": 1}, scope="account"
    )
    assert task is not None
    await task
    assert _ValidatingPlugin.saved == [({"a": 1}, "account")]
    assert await plugin_config_hooks.schedule_config_saved(
        hook_env, feature=_feature("codex_image"), account=None, config={}, scope="global"
    ) is None


# ─────────────────────────────────────────────────────
# 卡片取数与按钮
# ─────────────────────────────────────────────────────


class _CardPlugin(Plugin):
    key = "carded"
    display_name = "Carded"
    actions: list[str] = []

    async def on_ui_card(self, ctx: PluginContext, card_id: str, context: dict) -> dict | None:
        if card_id == "slow":
            await asyncio.sleep(5)
        if card_id == "broken":
            raise ValueError("boom")
        return {"calls": 3, "context": context}

    async def on_ui_action(self, ctx: PluginContext, action_id: str, context: dict) -> dict | None:
        type(self).actions.append(action_id)
        return {"message": "已刷新缓存"}


CARD_UI = {
    "cards": [
        {"id": "quota", "slot": "account.detail", "title": "配额", "source": {"action": "quota"},
         "blocks": [{"type": "stats", "items": [{"label": "调用", "path": "calls"}]}, {"type": "buttons", "items": [{"id": "refresh_cache", "label": "刷新缓存"}]}]},
        {"id": "slow", "slot": "dashboard", "title": "慢", "source": {"action": "slow"}, "layout": {"type": "kv"}},
        {"id": "broken", "slot": "dashboard", "title": "坏", "source": {"action": "broken"}, "layout": {"type": "kv"}},
    ]
}


@pytest.fixture
def card_env(monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis):
    async def _cards(db, key):  # noqa: ANN001, ANN202, ARG001
        return plugin_ui.normalize_ui_cards(CARD_UI, reads=[])

    async def _ctx(db, plugin_key, account_id):  # noqa: ANN001, ANN202, ARG001
        return SimpleNamespace(key=plugin_key), None, _CardPlugin, PluginContext(account_id=account_id or 0, feature_key=plugin_key)

    monkeypatch.setattr(plugin_ui_cards, "plugin_cards", _cards)
    monkeypatch.setattr(plugin_ui_cards, "_action_context", _ctx)
    _CardPlugin.actions = []
    return fake_redis


async def test_card_data_ok_timeout_and_error_only_affect_one_card(card_env, monkeypatch: pytest.MonkeyPatch) -> None:
    ok = await plugin_ui_cards.card_data(None, plugin_key="carded", card_id="quota", account_id=7)
    assert ok["status"] == "ok"
    assert ok["data"]["calls"] == 3
    assert ok["data"]["context"] == {"slot": "account.detail", "account_id": 7, "action": "quota"}

    monkeypatch.setattr(plugin_config_hooks, "hook_timeout_seconds", lambda hook: 0.05)
    slow = await plugin_ui_cards.card_data(None, plugin_key="carded", card_id="slow")
    assert slow["status"] == "error" and slow["message"].startswith("已停止工作")
    broken = await plugin_ui_cards.card_data(None, plugin_key="carded", card_id="broken")
    assert broken["status"] == "error" and "ValueError" in broken["message"]
    missing = await plugin_ui_cards.card_data(None, plugin_key="carded", card_id="nope")
    assert missing["status"] == "error"


async def test_card_action_calls_plugin_and_rejects_undeclared(card_env) -> None:
    result = await plugin_ui_cards.card_action(None, plugin_key="carded", card_id="quota", action_id="refresh_cache", account_id=7)
    assert result == {"ok": True, "message": "已刷新缓存", "refresh": True}
    assert _CardPlugin.actions == ["refresh_cache"]
    with pytest.raises(plugin_ui_cards.PluginCardError):
        await plugin_ui_cards.card_action(None, plugin_key="carded", card_id="quota", action_id="delete_all")


async def test_host_stats_card_formats_stats_and_resets(fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    class _DB:
        async def get(self, model, key):  # noqa: ANN001, ANN202
            return SimpleNamespace(display_name="视觉测试账号", phone="+86") if model.__name__ == "Account" else None

    plugin_stats.record_hook(account_id=1, plugin_key="demo", hook="on_event", event_type="message", outcome="ok", duration_ms=40)
    plugin_stats.record_hook(account_id=1, plugin_key="demo", hook="on_config_action", event_type="sync", outcome="fail", duration_ms=10, error=ValueError("缺少 API Key"))
    data = await plugin_ui_cards.card_data(_DB(), plugin_key="demo", card_id=plugin_ui_cards.HOST_STATS_CARD_ID)
    assert data["status"] == "ok", data
    summary = data["data"]["summary"]
    assert summary["calls"] == 2 and summary["failed"] == 1 and summary["failed_tone"] == "danger"
    rows = {row["key"]: row for row in data["data"]["hooks"]}
    assert rows["on_event"]["hook"] == "事件处理" and rows["on_event"]["account"] == "视觉测试账号"
    assert rows["on_config_action"]["health"] == "失败偏多"
    card = plugin_ui_cards._host_stats_card()  # noqa: SLF001
    assert [block["type"] for block in card["blocks"]] == ["stats", "list", "list", "table", "buttons"]
    result = await plugin_ui_cards.card_action(_DB(), plugin_key="demo", card_id=plugin_ui_cards.HOST_STATS_CARD_ID, action_id="reset_stats")
    assert result["refresh"] is True
    after = await plugin_ui_cards.card_data(_DB(), plugin_key="demo", card_id=plugin_ui_cards.HOST_STATS_CARD_ID)
    assert after["data"]["hooks"] == []


def test_ui_declaration_prefers_installed_manifest(tmp_path: Path) -> None:
    (tmp_path / "plugin.json").write_text(json.dumps({"ui": {"cards": []}, "reads": ["plugin.stats@1"]}), encoding="utf-8")
    installed = SimpleNamespace(manifest_json={"key": "x"}, installed_path=str(tmp_path))
    ui, reads = plugin_ui_cards.ui_declaration(SimpleNamespace(manifest={}), installed)
    assert ui == {"cards": []} and reads == ["plugin.stats@1"]


def test_host_stats_card_lists_sync_blocking_hooks() -> None:
    from app.services import plugin_ui_cards

    data = plugin_ui_cards._format_hook_rows(
        {
            "hooks": [
                {"hook": "middleware.outbound", "event_type": "-", "calls": 3, "ok": 3, "blocked": 2, "blocking_ms": 1500},
                {"hook": "on_event", "event_type": "message", "calls": 1, "ok": 1},
            ],
            "totals": {"calls": 4},
        },
        {},
    )
    assert data["blocking"] == [
        {
            "title": "出站中间件",
            "subtitle": "插件代码在事件循环上同步执行超过 200 毫秒",
            "trailing": "2 次 · 累计 1.5 秒",
            "tone": "warning",
        }
    ]
    card = plugin_ui_cards._host_stats_card()
    blocking_block = next(block for block in card["blocks"] if block.get("id") == "blocking")
    assert blocking_block["hide_when_empty"] is True
