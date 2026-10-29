"""P4a：插件自带页面（ui.pages + Bridge）、菜单项（ui.actions）、钩子只读 ctx.storage、ai.models@1 只增字段。

含兼容测试：未声明 ui.pages / ui.actions 的旧插件没有任何新入口、卡片接口与旧响应结构不变；
ai.models@1 旧字段原样保留。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import plugin_ui_cards, plugin_ui_pages
from app.worker.plugins import capabilities as caps_mod
from app.worker.plugins import plugin_ui
from app.worker.plugins.base import Plugin, PluginContext
from app.worker.plugins.host_read import ai_model_metadata
from app.worker.plugins.storage import PluginStorage, ReadOnlyPluginStorage
from app.worker.plugins.versioning import PLUGIN_API_VERSION


class _FakeRedis:
    """最小 KV Redis 替身（get / set / delete / mget / keys）。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:  # noqa: ARG002
        self.data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        return sum(1 for key in keys if self.data.pop(key, None) is not None)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.data.get(key) for key in keys]

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [key for key in self.data if key.startswith(prefix)]

    def hincrby(self, *_args: Any, **_kwargs: Any) -> None:  # 统计 flush 用，忽略
        return None

    def __getattr__(self, name: str) -> Any:
        async def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None

        return _noop

REPO = Path(__file__).resolve().parents[3]
DEMO_DIR = REPO / "examples" / "plugins" / "ui_page_demo"


def _demo_meta() -> dict[str, Any]:
    return json.loads((DEMO_DIR / "plugin.json").read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────
# 声明校验与兼容
# ─────────────────────────────────────────────────────


def test_contract_has_pages_section_and_version_3() -> None:
    contract = plugin_ui.load_contract()
    assert contract["contract_version"] >= 3
    assert plugin_ui.page_menus() == ["plugin.detail", "account.detail"]
    assert plugin_ui.page_bridge_permissions() == ["config.read", "cards.read", "actions.invoke"]
    csp = contract["pages"]["sandbox"]["csp"]
    assert "connect-src 'none'" in csp and "default-src 'none'" in csp and "form-action 'none'" in csp


def test_demo_example_declarations_are_clean() -> None:
    meta = _demo_meta()
    pages, actions, warnings = plugin_ui.normalize_ui_entries(meta["ui"])
    assert warnings == []
    assert [page["id"] for page in pages] == ["dashboard"]
    assert pages[0]["title"] == "ping 面板" and pages[0]["file"] == "ui/index.html"
    assert [(item["menu"], item["id"], item["page"]) for item in actions] == [
        ("plugin.detail", "summarize", None),
        ("account.detail", "open_dashboard", "dashboard"),
    ]
    assert plugin_ui.lint_ui_declaration(meta) == []


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"id": "a", "file": "../secret.html"}, "file"),
        ({"id": "a", "file": "/etc/passwd.html"}, "file"),
        ({"id": "a", "file": "ui/app.js"}, "file"),
        ({"id": "a", "file": "x.html", "bridge": ["token.read"]}, "bridge"),
        ({"id": "a", "file": "x.html", "menus": ["chat_row"]}, "menus"),
        ({"id": "bad id", "file": "x.html"}, "id"),
    ],
)
def test_invalid_pages_are_ignored_with_lint(raw: dict[str, Any], fragment: str) -> None:
    pages, warnings = plugin_ui.normalize_ui_pages({"pages": [raw, {"id": "ok", "file": "ok.html"}]})
    assert [page["id"] for page in pages] == ["ok"]
    assert any(fragment in item for item in warnings), warnings


def test_menu_action_must_reference_declared_page() -> None:
    actions, warnings = plugin_ui.normalize_ui_actions(
        {"actions": [{"id": "x", "page": "missing"}, {"id": "y", "menu": "account.detail", "confirm": True}]}
    )
    assert [item["id"] for item in actions] == ["y"]
    assert actions[0]["confirm"] == "确定执行？"
    assert any("missing" in item for item in warnings)


@pytest.mark.parametrize("ui", [None, {}, {"cards": []}, {"cards": [{"id": "c", "slot": "dashboard", "source": {"action": "c"}, "layout": {"type": "kv"}}]}])
def test_legacy_plugins_without_pages_or_actions_have_no_entries(ui: Any) -> None:
    """兼容：旧插件（没有 ui，或只有 ui.cards）不产生任何页面 / 菜单项，也没有新增 lint。"""

    pages, actions, warnings = plugin_ui.normalize_ui_entries(ui)
    assert (pages, actions, warnings) == ([], [], [])
    manifest = {"ui": ui} if ui is not None else {}
    assert plugin_ui.lint_ui_declaration(manifest) == plugin_ui.normalize_ui_cards(ui, reads=[])[1]


class _DB:
    def __init__(self, feature: Any = None, installed: Any = None) -> None:
        self.feature = feature
        self.installed = installed

    async def get(self, model, key):  # noqa: ANN001, ANN202, ARG002
        return {"Feature": self.feature, "InstalledPlugin": self.installed}.get(model.__name__)

    async def execute(self, _stmt):  # noqa: ANN001, ANN202
        return SimpleNamespace(scalar_one_or_none=lambda: True, scalars=lambda: SimpleNamespace(all=lambda: []))


def _demo_db(tmp_path: Path) -> _DB:
    target = tmp_path / "ui_page_demo"
    shutil.copytree(DEMO_DIR, target)
    (target / ".secret.html").write_text("<p>hidden</p>", encoding="utf-8")
    (target / "ui" / ".secret.html").write_text("<p>hidden</p>", encoding="utf-8")
    (target / "ui" / "app.css").write_text("body{}", encoding="utf-8")
    meta = _demo_meta()
    feature = SimpleNamespace(key="ui_page_demo", display_name="插件页面示例", manifest=meta)
    installed = SimpleNamespace(key="ui_page_demo", manifest_json=meta, installed_path=str(target))
    return _DB(feature, installed)


async def test_list_entries_for_legacy_plugin_is_empty() -> None:
    legacy = SimpleNamespace(key="old", display_name="旧插件", manifest={"config_schema": {"type": "object"}})
    db = _DB(legacy, SimpleNamespace(key="old", manifest_json={"key": "old"}, installed_path=""))
    pages, actions, warnings = await plugin_ui_pages.list_entries(db, menu="plugin.detail", plugin_key="old")
    assert (pages, actions, warnings) == ([], [], [])


async def test_list_entries_filters_by_menu(tmp_path: Path) -> None:
    db = _demo_db(tmp_path)
    pages, actions, _ = await plugin_ui_pages.list_entries(db, menu="plugin.detail", plugin_key="ui_page_demo")
    assert [p["page_id"] for p in pages] == ["dashboard"]
    assert [a["action_id"] for a in actions] == ["summarize"]
    pages, actions, _ = await plugin_ui_pages.list_entries(db, menu="account.detail", plugin_key="ui_page_demo", account_id=3)
    assert [a["action_id"] for a in actions] == ["open_dashboard"] and actions[0]["page_id"] == "dashboard"
    with pytest.raises(plugin_ui_pages.PluginPageError):
        await plugin_ui_pages.list_entries(db, menu="chat_row", plugin_key="ui_page_demo")


# ─────────────────────────────────────────────────────
# 会话 token 与资源
# ─────────────────────────────────────────────────────


def test_page_token_roundtrip_tamper_and_expiry() -> None:
    token, expires_at = plugin_ui_pages.issue_page_token("demo", "dashboard", 5, now=1000)
    assert expires_at == 1000 + 3600
    payload = plugin_ui_pages.verify_page_token(token, now=1001)
    assert (payload["k"], payload["p"], payload["a"]) == ("demo", "dashboard", 5)
    body, _, sig = token.partition(".")
    with pytest.raises(plugin_ui_pages.PluginPageError):
        plugin_ui_pages.verify_page_token(body + "." + sig[::-1], now=1001)
    with pytest.raises(plugin_ui_pages.PluginPageError) as exc:
        plugin_ui_pages.verify_page_token(token, now=expires_at + 1)
    assert exc.value.code == "PLUGIN_PAGE_TOKEN_EXPIRED"
    assert "jwt" not in token.lower()


async def test_session_and_assets_inject_csp_and_bridge(tmp_path: Path) -> None:
    db = _demo_db(tmp_path)
    session = await plugin_ui_pages.open_page_session(db, plugin_key="ui_page_demo", page_id="dashboard", account_id=None)
    assert session["url"].startswith("/api/plugin-ui/page-assets/") and session["url"].endswith("/ui/index.html")
    data, content_type = await plugin_ui_pages.read_asset(db, token=session["token"], path="ui/index.html")
    html = data.decode()
    assert content_type.startswith("text/html")
    head = html.index("<head>") + len("<head>")
    assert html[head:].startswith('<meta http-equiv="Content-Security-Policy"')
    assert "window.telepilot" in html and "connect-src 'none'" in html
    headers = plugin_ui_pages.asset_headers()
    assert "connect-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "SAMEORIGIN" and headers["Referrer-Policy"] == "no-referrer"
    for bad in ("../plugin.json", "plugin.json", "plugin.py", "README.md", ".secret.html", "ui/.secret.html", "ui/../../etc/passwd", "nope.html"):
        with pytest.raises(plugin_ui_pages.PluginPageError):
            await plugin_ui_pages.read_asset(db, token=session["token"], path=bad)
    # 页面目录内的非 HTML 资源可以读取，不注入
    data, content_type = await plugin_ui_pages.read_asset(db, token=session["token"], path="ui/app.css")
    assert data == b"body{}" and content_type.startswith("text/css")


def test_inject_without_head_wraps_document() -> None:
    html = plugin_ui_pages.inject_into_html("<p>hi</p>")
    assert html.startswith("<!doctype html><html><head><meta http-equiv=\"Content-Security-Policy\"")
    assert "<p>hi</p>" in html


# ─────────────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────────────


def test_public_config_drops_secrets() -> None:
    schema = {
        "type": "object",
        "properties": {
            "greeting": {"type": "string"},
            "api_token": {"type": "string", "format": "password"},
            "tokens": {"type": "array", "x-secret-list": {"field": "token"}, "items": {"type": "object"}},
            "nested": {"type": "object", "properties": {"password": {"type": "string"}, "ok": {"type": "integer"}}},
        },
    }
    config = {
        "greeting": "你好",
        "api_token": "sk-123",
        "tokens": [{"token": "abc"}],
        "nested": {"password": "p", "ok": 1},
        "bot_token": "123:ABC",
    }
    out = plugin_ui_pages.public_config(config, schema, "demo")
    assert out == {"greeting": "你好", "nested": {"ok": 1}}


class _PagePlugin(Plugin):
    key = "ui_page_demo"
    calls: list[tuple[str, dict]] = []

    async def on_ui_action(self, ctx: PluginContext, action_id: str, context: dict) -> dict | None:
        type(self).calls.append((action_id, context))
        writable = True
        try:
            await ctx.storage.set("x", 1)
        except PermissionError:
            writable = False
        return {"message": "ok", "data": {"writable": writable, "pings": await ctx.storage.get("pings", 0)}}


@pytest.fixture
def bridge_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    redis = _FakeRedis()
    import app.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    db = _demo_db(tmp_path)

    async def _ctx(db_, plugin_key, account_id):  # noqa: ANN001, ANN202, ARG001
        storage = ReadOnlyPluginStorage(PluginStorage(account_id=account_id or 0, plugin_key=plugin_key, redis=redis))
        return SimpleNamespace(key=plugin_key), None, _PagePlugin, PluginContext(account_id=account_id or 0, feature_key=plugin_key, storage=storage)

    async def _global_config(db_, key):  # noqa: ANN001, ANN202, ARG001
        return {"greeting": "嗨", "api_token": "sk-secret"}

    from app.services import feature_service

    monkeypatch.setattr(plugin_ui_cards, "_action_context", _ctx)
    monkeypatch.setattr(feature_service, "get_plugin_global_config", _global_config)
    _PagePlugin.calls = []
    return db, redis


async def test_bridge_ready_config_action_and_permission(bridge_env, monkeypatch: pytest.MonkeyPatch) -> None:
    db, redis = bridge_env
    await PluginStorage(account_id=0, plugin_key="ui_page_demo", redis=redis).set("pings", 7)
    ready = await plugin_ui_pages.bridge_call(db, plugin_key="ui_page_demo", page_id="dashboard", method="ready", params={}, account_id=None)
    assert ready["protocol"] == 1 and ready["bridge"] == ["config.read", "cards.read", "actions.invoke"]
    cfg = await plugin_ui_pages.bridge_call(db, plugin_key="ui_page_demo", page_id="dashboard", method="config.get", params={}, account_id=None)
    assert cfg == {"scope": "global", "config": {"greeting": "嗨"}}
    result = await plugin_ui_pages.bridge_call(
        db, plugin_key="ui_page_demo", page_id="dashboard", method="action.invoke", params={"id": "summarize", "payload": {"a": 1}}, account_id=None
    )
    assert result["data"] == {"writable": False, "pings": 7}
    assert _PagePlugin.calls[0][0] == "summarize"
    assert _PagePlugin.calls[0][1]["page_id"] == "dashboard" and _PagePlugin.calls[0][1]["payload"] == {"a": 1}
    # 页面白名单外的 action、未知方法、宿主卡片都被拒绝
    for method, params in (("action.invoke", {"id": "delete_all"}), ("token.get", {}), ("card.data", {"card_id": "_host.stats"})):
        with pytest.raises(plugin_ui_pages.PluginPageError):
            await plugin_ui_pages.bridge_call(db, plugin_key="ui_page_demo", page_id="dashboard", method=method, params=params, account_id=None)


async def test_bridge_denies_undeclared_permissions(bridge_env) -> None:
    db, _redis = bridge_env
    meta = _demo_meta()
    meta["ui"]["pages"][0]["bridge"] = []
    db.feature.manifest = meta
    db.installed.manifest_json = meta
    with pytest.raises(plugin_ui_pages.PluginPageError) as exc:
        await plugin_ui_pages.bridge_call(db, plugin_key="ui_page_demo", page_id="dashboard", method="config.get", params={}, account_id=None)
    assert exc.value.code == "BRIDGE_PERMISSION_DENIED"
    # ready 总可用
    ready = await plugin_ui_pages.bridge_call(db, plugin_key="ui_page_demo", page_id="dashboard", method="ready", params={}, account_id=None)
    assert ready["bridge"] == []


async def test_menu_action_context(bridge_env) -> None:
    db, _redis = bridge_env
    result = await plugin_ui_pages.invoke_ui_action(
        db, plugin_key="ui_page_demo", action_id="summarize", account_id=4, context={"menu": "plugin.detail", "page_id": None, "payload": {}}
    )
    assert result["ok"] is True and result["message"] == "ok"
    assert _PagePlugin.calls[-1][1] == {"account_id": 4, "menu": "plugin.detail", "page_id": None, "payload": {}}


# ─────────────────────────────────────────────────────
# 只读 storage、模型清单、能力
# ─────────────────────────────────────────────────────


async def test_readonly_storage_reads_and_denies_writes() -> None:
    redis = _FakeRedis()
    inner = PluginStorage(account_id=2, plugin_key="demo", redis=redis)
    await inner.set("k", {"v": 1})
    ro = ReadOnlyPluginStorage(inner)
    assert await ro.get("k") == {"v": 1}
    assert await ro.get_all() == {"k": {"v": 1}}
    assert ro.read_only is True and ro.available is True and ro.account_id == 2
    for call in (lambda: ro.set("k", 2), lambda: ro.delete("k"), lambda: ro.incr("k")):
        with pytest.raises(PermissionError):
            await call()


async def test_build_main_process_context_storage_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import plugin_config_actions

    redis = _FakeRedis()
    import app.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    feature = SimpleNamespace(key="demo", manifest={})
    plain = await plugin_config_actions.build_main_process_context(None, account=None, feature=feature, plugin_cls=Plugin, config={})
    assert plain.storage is None  # 配置动作保持原样
    ro = await plugin_config_actions.build_main_process_context(
        None, account=None, feature=feature, plugin_cls=Plugin, config={}, readonly_storage=True
    )
    assert isinstance(ro.storage, ReadOnlyPluginStorage)


def test_ai_models_metadata_is_additive() -> None:
    provider = SimpleNamespace(name="OpenAI", has_api_key=True, cost_tier=3)
    meta = ai_model_metadata(provider, {"id": "gpt-x", "display_name": "GPT X"}, "gpt-x", True)
    assert meta == {
        "display_name": "GPT X",
        "label": "OpenAI · GPT X",
        "available": True,
        "has_api_key": True,
        "cost_tier": 3,
        "cost_tier_label": "旗舰",
    }
    no_key = ai_model_metadata(SimpleNamespace(name="P", has_api_key=False, cost_tier=None), {"id": "m"}, "m", True)
    assert no_key["available"] is False and no_key["display_name"] == "m" and no_key["cost_tier"] == 2
    disabled = ai_model_metadata(SimpleNamespace(name="P", has_api_key=True, cost_tier=1), {"id": "m"}, "m", False)
    assert disabled["available"] is False and disabled["cost_tier_label"] == "便宜"
    # 新字段不会覆盖旧字段名
    assert not {"provider_id", "provider_name", "provider", "model", "enabled", "default", "tags"} & set(meta)


async def test_ai_models_resource_keeps_legacy_fields() -> None:
    from app.worker.plugins import loader

    class _AI:
        async def list_providers(self):  # noqa: ANN202
            return [
                SimpleNamespace(
                    id=1, name="OpenAI", provider="openai", default_model="gpt-x", tags=["fast"], cost_tier=1, has_api_key=True,
                    models=[{"id": "gpt-x", "enabled": True, "display_name": "GPT X"}, {"id": "old", "enabled": False}],
                )
            ]

    state = SimpleNamespace(account_id=1, ignored_peers=set(), redis=None, sudo_users=set(), owner_tg_user_id=None)
    manifest = SimpleNamespace(reads=["ai.models@1"])
    host = loader._build_plugin_host(state, "demo", manifest, _AI(), None)  # noqa: SLF001
    items = await host.read("ai.models@1")
    assert len(items) == 1
    item = items[0]
    legacy = {"provider_id": 1, "provider_name": "OpenAI", "provider": "openai", "model": "gpt-x", "enabled": True, "default": True, "tags": ["fast"]}
    assert {key: item[key] for key in legacy} == legacy
    assert item["display_name"] == "GPT X" and item["available"] is True and item["cost_tier"] == 1


def test_p4a_capabilities_and_plugin_api() -> None:
    assert tuple(int(x) for x in PLUGIN_API_VERSION.split(".")) >= (1, 4)
    for name in ("ui.pages", "ui.actions", "hooks.storage_read", "ai.models.metadata"):
        item = caps_mod.CAPABILITY_INDEX[name]
        assert item.since == caps_mod._P4A_RELEASE  # noqa: SLF001
        assert item.plugin_api == "1.4"
    ctx = PluginContext(account_id=1, feature_key="demo")
    assert ctx.capabilities.has("ui.pages") and ctx.capabilities.has("hooks.storage_read")


# ─────────────────────────────────────────────────────
# 接口层：旧接口结构不变、页面资源响应头
# ─────────────────────────────────────────────────────


def test_existing_card_schemas_are_unchanged_for_old_apps() -> None:
    """兼容：旧 App（0.14.x）只用卡片接口，这些响应模型的字段一个不多一个不少。"""

    from app.schemas import plugin_ui as schemas

    assert list(schemas.PluginUICard.model_fields) == [
        "plugin_key", "plugin_name", "card_id", "slot", "title", "subtitle", "source_kind", "blocks", "refresh_seconds", "host",
    ]
    assert list(schemas.PluginUICardsResponse.model_fields) == ["slot", "cards", "warnings"]
    assert list(schemas.PluginUICardData.model_fields) == ["plugin_key", "card_id", "status", "data", "message", "updated_at", "duration_ms"]
    assert list(schemas.PluginUICardActionResponse.model_fields) == ["ok", "message", "refresh"]


def test_new_routes_are_registered() -> None:
    from app.api import plugin_ui as api
    from app.main import app

    assert app.openapi()["paths"].get("/api/plugin-ui/entries")
    paths = {getattr(route, "path", "") for route in api.router.routes}
    for path in (
        "/api/plugin-ui/entries",
        "/api/plugin-ui/pages/{plugin_key}/{page_id}/session",
        "/api/plugin-ui/page-assets/{token}/{path:path}",
        "/api/plugin-ui/pages/{plugin_key}/{page_id}/bridge",
        "/api/plugin-ui/actions/{plugin_key}/{action_id}",
        "/api/plugin-ui/cards",
    ):
        assert path in paths, path


async def test_asset_endpoint_sets_strict_headers(tmp_path: Path) -> None:
    from app.api import plugin_ui as api

    db = _demo_db(tmp_path)
    session = await plugin_ui_pages.open_page_session(db, plugin_key="ui_page_demo", page_id="dashboard", account_id=None)
    ok = await api.plugin_ui_page_asset(session["token"], "ui/index.html", db)
    assert ok.status_code == 200 and "connect-src 'none'" in ok.headers["content-security-policy"]
    assert ok.headers["x-frame-options"] == "SAMEORIGIN" and ok.headers["cache-control"] == "no-store"
    bad = await api.plugin_ui_page_asset("forged.token", "ui/index.html", db)
    assert bad.status_code == 404 and "connect-src 'none'" in bad.headers["content-security-policy"]
