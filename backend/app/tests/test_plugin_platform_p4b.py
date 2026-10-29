"""P4b 非资金业务下沉：entry_policy 声明、扩展点 provides（ai.image_backend）、math10 规则迁移、规则编辑器 slot。

每项都覆盖两种兼容组合：

- 旧插件 + 新服务器：插件没有新声明时，宿主走原来的兜底 / 写死路径，结果与 0.110.0-beta.2 相同；
- 新插件 + 旧服务器：新插件保留旧字段，旧服务器忽略新字段（这里用“去掉新字段后走旧逻辑”模拟），结果与新服务器相同。
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import plugin_compat
from app.schemas.feature import _normalize_rule_editor
from app.services import account_bot_service, interaction_rule_migration
from app.services.remote_plugin_service import PluginMetadataSchema
from app.worker import ai_runtime
from app.worker import command as wcmd
from app.worker.command import CommandContext
from app.worker.plugins import capabilities as caps_mod
from app.worker.plugins import extension_points as ext
from app.worker.plugins.manifest import Manifest


def _write_plugin(tmp_path, key: str, meta: dict) -> None:
    plugin_dir = tmp_path / key
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": key, **meta}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def installed(monkeypatch, tmp_path):
    monkeypatch.setattr(account_bot_service, "settings", SimpleNamespace(plugins_installed_path=tmp_path))
    monkeypatch.setattr(account_bot_service.BUILTIN_FEATURES, "manifest_for", lambda _key: None)
    return tmp_path


# ─────────────────────────────────────────────────────
# B3：entry_policy
# ─────────────────────────────────────────────────────


def test_capabilities_registered_for_p4b() -> None:
    for name in ("interaction.entry_policy", "provides.ai.image_backend", "ui.rule_editor"):
        item = caps_mod.CAPABILITY_INDEX[name]
        assert item.plugin_api == "1.5"
        assert item.since == caps_mod._P4B_RELEASE


@pytest.mark.parametrize(
    ("module_key", "entry_key"),
    sorted(
        plugin_compat.FALLBACK_CHAT_SESSION_MODULE_ENTRIES
        | plugin_compat.FALLBACK_NO_PRIZE_MODULE_ENTRIES
        | plugin_compat.FALLBACK_SOLO_OWNER_MODULE_ENTRIES
    ),
)
def test_old_plugin_without_declaration_keeps_fallback_table(installed, module_key, entry_key) -> None:
    """旧插件 + 新服务器：没装 plugin.json / 没声明入口时，兜底入口表结果不变。"""

    assert account_bot_service._declared_module_entry_session_scope(module_key, entry_key) == (
        plugin_compat.fallback_entry_session_scope(module_key, entry_key)
    )
    assert account_bot_service._declared_module_entry_participant_policy(module_key, entry_key) == (
        plugin_compat.fallback_entry_participant_policy(module_key, entry_key)
    )
    assert account_bot_service.declared_module_entry_has_field(module_key, entry_key, "prize") == (
        plugin_compat.fallback_entry_has_field(module_key, entry_key, "prize")
    )
    keys = plugin_compat.fallback_entry_keys(module_key)
    assert account_bot_service._declared_module_single_entry_key(module_key) == (keys[0] if len(keys) == 1 else None)


def test_old_plugin_entries_without_policy_still_use_fallback(installed) -> None:
    _write_plugin(installed, "blackjack", {"interaction_entries": [{"key": "start_blackjack", "session_scope": "user"}]})
    assert account_bot_service._declared_module_entry_participant_policy("blackjack", "start_blackjack") == "solo_owner"
    assert account_bot_service.module_declares_entry_policy("blackjack") is False


def test_entry_policy_declared_disables_fallback_table(installed) -> None:
    _write_plugin(
        installed,
        "blackjack",
        {"interaction_entries": [{"key": "start_blackjack", "session_scope": "user", "entry_policy": {}}]},
    )
    assert account_bot_service.module_declares_entry_policy("blackjack") is True
    assert account_bot_service._declared_module_entry_participant_policy("blackjack", "start_blackjack") is None
    # 声明了 entry_policy 的插件，未知入口也不再从兜底表推断
    _write_plugin(installed, "dice_grid_hunt", {"interaction_entries": [{"key": "x", "entry_policy": {}}, {"key": "y"}]})
    assert account_bot_service._declared_module_single_entry_key("dice_grid_hunt") is None
    assert account_bot_service._declared_module_entry_session_scope("dice_grid_hunt", "answer_dice_grid_hunt") is None


def test_entry_policy_fields_are_honoured(installed) -> None:
    _write_plugin(
        installed,
        "pt_promote",
        {
            "interaction_entries": [
                {
                    "key": "promote_torrent",
                    "input_schema": {"type": "object", "properties": {"prize": {"type": "integer"}, "torrent_id": {"type": "string"}}},
                    "entry_policy": {
                        "session_scope": "user",
                        "participant_policy": "notify_only",
                        "prize": False,
                        "default_start_keywords": ["促销", "促销"],
                    },
                }
            ]
        },
    )
    entry = account_bot_service.declared_module_entry_manifest("pt_promote", "promote_torrent")
    assert entry["session_scope"] == "user"
    assert entry["participant_policy"] == "notify_only"
    assert entry["entry_policy"]["default_start_keywords"] == ["促销"]
    assert account_bot_service.declared_module_entry_has_field("pt_promote", "promote_torrent", "prize") is False
    assert account_bot_service.declared_module_default_start_keywords("pt_promote", "promote_torrent") == ["促销"]
    rules = account_bot_service.normalize_interaction_rules(
        [{"id": "p", "action": "module", "module_key": "pt_promote", "module_prize": 50, "math_prize": 50}]
    )
    assert rules[0]["module_action"] == "promote_torrent"
    assert rules[0]["module_prize"] is None
    assert rules[0]["module_session_scope"] == "user"
    assert rules[0]["participant_policy"] == "notify_only"


def test_top_level_fields_win_over_entry_policy() -> None:
    entry = account_bot_service.normalize_interaction_entry_manifest(
        {"key": "k", "session_scope": "chat", "participant_policy": "open_race",
         "entry_policy": {"session_scope": "user", "participant_policy": "solo_owner", "prize": "yes"}}
    )
    assert entry["session_scope"] == "chat"
    assert entry["participant_policy"] == "open_race"
    assert "prize" not in entry["entry_policy"]  # 非 bool 丢弃


NEW_STYLE_ENTRIES = {
    # 新插件写法：新字段 entry_policy + 保留旧字段（旧服务器只认旧字段）
    "game24": [{"key": "start_paid_game", "session_scope": "chat", "participant_policy": "open_race",
                "input_schema": {"type": "object", "properties": {"prize": {"type": "integer"}}},
                "entry_policy": {"session_scope": "chat", "participant_policy": "open_race", "prize": True}}],
    "pt_promote": [{"key": "promote_torrent", "session_scope": "user", "participant_policy": "notify_only",
                    "input_schema": {"type": "object", "properties": {"torrent_id": {"type": "string"}}},
                    "entry_policy": {"session_scope": "user", "participant_policy": "notify_only", "prize": False}}],
    "blackjack": [{"key": "start_blackjack", "session_scope": "user", "participant_policy": "solo_owner",
                   "input_schema": {"type": "object", "properties": {"prize": {"type": "integer"}}},
                   "entry_policy": {"session_scope": "user", "participant_policy": "solo_owner", "prize": True}}],
}


def _strip_entry_policy(entries: list[dict]) -> list[dict]:
    out = copy.deepcopy(entries)
    for item in out:
        item.pop("entry_policy", None)
    return out


@pytest.mark.parametrize("module_key", sorted(NEW_STYLE_ENTRIES))
def test_new_plugin_on_old_server_resolves_the_same(installed, module_key) -> None:
    """新插件 + 旧服务器：旧服务器忽略 entry_policy，只读旧字段，结论必须与新服务器一致。"""

    entries = NEW_STYLE_ENTRIES[module_key]
    entry_key = entries[0]["key"]

    def resolve() -> tuple:
        return (
            account_bot_service._declared_module_single_entry_key(module_key),
            account_bot_service._declared_module_entry_session_scope(module_key, entry_key),
            account_bot_service._declared_module_entry_participant_policy(module_key, entry_key),
            account_bot_service.declared_module_entry_has_field(module_key, entry_key, "prize"),
        )

    _write_plugin(installed, module_key, {"interaction_entries": entries})
    new_server = resolve()
    _write_plugin(installed, module_key, {"interaction_entries": _strip_entry_policy(entries)})
    old_server_view = resolve()
    assert new_server == old_server_view


# ─────────────────────────────────────────────────────
# B8：扩展点 provides / ai.image_backend
# ─────────────────────────────────────────────────────


def test_normalize_provides_and_unknown() -> None:
    assert ext.normalize_provides([" ai.image_backend ", "ai.image_backend", "", None, "x.y"]) == ["ai.image_backend", "x.y"]
    assert ext.normalize_provides("ai.image_backend") == []
    assert ext.unknown_provides(["ai.image_backend", "x.y"]) == ["x.y"]
    assert Manifest(key="k", display_name="K", version="1.0.0").to_dict()["provides"] == []


@pytest.mark.parametrize(
    ("backend", "explicit", "providers", "expected"),
    [
        ("codex_image", None, ["new_image"], ("codex_image", "legacy")),  # 旧模板固定旧插件
        ("codex_image", "my_image", [], ("my_image", "explicit")),
        ("plugin", None, ["new_image", "other"], ("new_image", "capability")),
        ("plugin", None, [], ("codex_image", "fallback")),  # 旧插件 + 新服务器
        ("plugin", "my_image", ["new_image"], ("my_image", "explicit")),
    ],
)
def test_select_image_backend_plugin(backend, explicit, providers, expected) -> None:
    assert (
        ext.select_image_backend_plugin(
            image_backend=backend,
            image_plugin_key=explicit,
            providers=providers,
            legacy_default=plugin_compat.DEFAULT_IMAGE_BACKEND_PLUGIN,
        )
        == expected
    )


def test_command_schema_image_backend_defaults_and_compat() -> None:
    from app.schemas.command import CommandTemplateCreate

    new = CommandTemplateCreate(name="image", type="ai", config={"mode": "image"})
    assert new.config["image_backend"] == "plugin"
    old = CommandTemplateCreate(name="image", type="ai", config={"mode": "image", "image_backend": "codex_image"})
    assert old.config["image_backend"] == "codex_image"
    with pytest.raises(ValueError):
        CommandTemplateCreate(name="image", type="ai", config={"mode": "image", "image_backend": "llm"})
    with pytest.raises(ValueError):
        CommandTemplateCreate(name="image", type="ai", config={"mode": "image", "image_backend": "dalle"})


@pytest.fixture
def _ai_ctx():
    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))
    yield
    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "providers", "expected_key"),
    [
        ({"mode": "image", "image_backend": "codex_image"}, ["new_image"], "codex_image"),
        ({"mode": "image", "image_backend": "plugin"}, ["new_image"], "new_image"),
        ({"mode": "image", "image_backend": "plugin"}, [], "codex_image"),
        ({"mode": "image"}, [], "codex_image"),
        ({"mode": "image"}, ["new_image"], "new_image"),
        ({"mode": "image", "image_backend": "plugin", "image_plugin_key": "pinned"}, ["new_image"], "pinned"),
    ],
)
async def test_ai_image_mode_selects_plugin_by_capability(monkeypatch, _ai_ctx, config, providers, expected_key) -> None:
    from app.worker.plugins import loader

    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(wcmd, "dispatch_plugin_command", dispatched)
    monkeypatch.setattr(loader, "plugins_providing", lambda _aid, point: list(providers) if point == "ai.image_backend" else [])
    event = AsyncMock()
    await ai_runtime.invoke(AsyncMock(), event, ["画一只猫"], {"name": "image", "type": "ai", "config": config}, 1)
    assert dispatched.await_args.kwargs["plugin_key"] == expected_key
    event.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_image_missing_backend_messages(monkeypatch, _ai_ctx) -> None:
    from app.worker.plugins import loader

    monkeypatch.setattr(wcmd, "dispatch_plugin_command", AsyncMock(return_value=False))
    monkeypatch.setattr(loader, "plugins_providing", lambda _aid, _point: [])
    event = AsyncMock()
    await ai_runtime.invoke(AsyncMock(), event, ["x"], {"name": "image", "type": "ai", "config": {"mode": "image"}}, 1)
    assert "codex_image 插件命令不可用" in event.edit.await_args.args[0]  # 旧文案保持
    monkeypatch.setattr(loader, "plugins_providing", lambda _aid, _point: ["new_image"])
    event = AsyncMock()
    await ai_runtime.invoke(AsyncMock(), event, ["x"], {"name": "image", "type": "ai", "config": {"mode": "image"}}, 1)
    assert "new_image" in event.edit.await_args.args[0]


def test_plugins_providing_reads_loaded_manifests(monkeypatch) -> None:
    from app.worker.plugins import loader

    class _WithProvides:
        _manifest = Manifest(key="new_image", display_name="N", version="1.0.0", provides=["ai.image_backend"])

    class _Old:
        _manifest = Manifest(key="codex_image", display_name="C", version="1.0.0")

    state = SimpleNamespace(instances={"codex_image": _Old(), "new_image": _WithProvides()})
    monkeypatch.setitem(loader._STATES, 4242, state)
    assert loader.plugins_providing(4242, "ai.image_backend") == ["new_image"]
    assert loader.plugins_providing(999999, "ai.image_backend") == []


def test_loader_merges_provides_from_plugin_json(tmp_path) -> None:
    from app.worker.plugins import loader

    (tmp_path / "plugin.json").write_text(json.dumps({"provides": ["ai.image_backend", "ai.image_backend"]}), encoding="utf-8")
    manifest = Manifest(key="x", display_name="X", version="1.0.0")
    loader._merge_static_plugin_api(tmp_path, manifest)
    assert manifest.provides == ["ai.image_backend"]


def test_plugin_metadata_accepts_provides_and_old_files() -> None:
    meta = PluginMetadataSchema.model_validate({"name": "x", "version": "1.0.0", "provides": ["ai.image_backend", "future.point"]})
    assert meta.provides == ["ai.image_backend", "future.point"]
    old = PluginMetadataSchema.model_validate({"name": "x", "version": "1.0.0"})
    assert old.provides == []
    with pytest.raises(ValueError):
        PluginMetadataSchema.model_validate({"name": "x", "version": "1.0.0", "provides": "ai.image_backend"})


# ─────────────────────────────────────────────────────
# W4：ui.rule_editor / slot interaction.rule_editor
# ─────────────────────────────────────────────────────


def test_rule_editor_normalization() -> None:
    raw = {
        "entries": {
            "start_paid_game": {
                "hint": " 发起一局 24 点 ",
                "start_keywords_placeholder": "开24点",
                "default_start_keywords": ["开24点", 3, ""],
                "schema": {"type": "object", "properties": {"timeout": {"type": "integer", "title": "限时"}}},
                "defaults": {"timeout": 60},
                "unknown": True,
            },
            "": {"hint": "x"},
            "bad": "nope",
        }
    }
    out = _normalize_rule_editor(raw)
    assert list(out["entries"]) == ["start_paid_game"]
    spec = out["entries"]["start_paid_game"]
    assert spec["hint"] == "发起一局 24 点"
    assert spec["default_start_keywords"] == ["开24点"]
    assert spec["defaults"] == {"timeout": 60}
    assert "unknown" not in spec
    assert _normalize_rule_editor(None) == {}
    assert _normalize_rule_editor({"entries": []}) == {}


@pytest.mark.asyncio
async def test_rule_editor_slot_requires_plugin_key() -> None:
    from app.services import plugin_ui_cards

    with pytest.raises(plugin_ui_cards.PluginCardError):
        await plugin_ui_cards.list_cards(AsyncMock(), slot="interaction.rule_editor")


# ─────────────────────────────────────────────────────
# B2：math10 规则迁移（golden：迁移前后交给插件的规则逐字段相同）
# ─────────────────────────────────────────────────────

LEGACY_MATH10_RULES = [
    {"id": "kw-default", "action": "math10", "chat_ids": [-100123], "trigger_mode": "keyword", "math_prize": 456},
    {"id": "pay-default", "action": "math10", "chat_ids": [-100123]},
    {"id": "pay-amount", "action": "math10", "trigger_mode": "payment", "amount": 10, "math_prize": 0},
    {"id": "both-keywords", "action": "math10", "trigger_mode": "both", "module_start_keywords": ["算数"], "concurrency": "user"},
    {"id": "module-prize", "action": "math10", "trigger_mode": "keyword", "math_prize": 5, "module_prize": 9},
    {"id": "custom-entry", "action": "math10", "module_action": "start_math_game", "module_session_scope": "none", "concurrency": "user"},
    {"id": "bad-values", "action": "math10", "trigger_mode": "bogus", "amount": "abc", "math_prize": "x", "concurrency": "x",
     "module_config": {"note": 1}, "valid_seconds": 5},
]


def _effective_old(rule: dict) -> dict:
    from app.services import account_bot_runtime

    normalized = account_bot_service.normalize_interaction_rules([rule])[0]
    return account_bot_runtime._math10_compat_module_rule(normalized)


def _effective_new(rule: dict) -> dict:
    migrated = interaction_rule_migration.migrate_legacy_math10_rule(rule)
    return account_bot_service.normalize_interaction_rules([migrated])[0]


@pytest.mark.parametrize("rule", LEGACY_MATH10_RULES, ids=[item["id"] for item in LEGACY_MATH10_RULES])
@pytest.mark.parametrize("plugin_state", ["not_installed", "current_math10", "math10_with_entry_policy"])
def test_math10_migration_is_equivalent_on_launch_path(installed, rule, plugin_state) -> None:
    if plugin_state == "current_math10":
        _write_plugin(installed, "math10", {"interaction_entries": [
            {"key": "start_math_game", "session_scope": "chat",
             "input_schema": {"type": "object", "properties": {"prize": {"type": "integer"}, "valid_seconds": {"type": "integer"}}}}]})
    elif plugin_state == "math10_with_entry_policy":
        _write_plugin(installed, "math10", {"interaction_entries": [
            {"key": "start_math_game", "session_scope": "chat",
             "input_schema": {"type": "object", "properties": {"prize": {"type": "integer"}}},
             "entry_policy": {"session_scope": "chat", "prize": True, "default_start_keywords": ["发十以内算数"]}}]})
    if rule.get("module_action") == "start_math_game" and plugin_state != "not_installed":
        pytest.skip("自定义入口与已声明入口同名：迁移脚本的安全闸只针对 start_math10，另由 test_math10_migration_guard 覆盖")
    old = _effective_old(rule)
    new = _effective_new(rule)
    assert new == old


def test_math10_migration_keeps_other_rules_and_backup_roundtrip() -> None:
    stored = {"enabled": True, "rules": [{"id": "n", "action": "notice"}, *copy.deepcopy(LEGACY_MATH10_RULES[:2])]}
    new_value, backup, report = interaction_rule_migration.plan_account_migration(7, stored)
    assert [r["action"] for r in new_value["rules"]] == ["notice", "module", "module"]
    assert set(backup) == {"kw-default", "pay-default"}
    assert len(report.rules_migrated) == 2
    # 未编辑 → 全部恢复
    restored, rb = interaction_rule_migration.plan_account_rollback(7, new_value, {"rules": backup})
    assert restored == stored
    assert len(rb.rules_restored) == 2
    # 编辑过 → 跳过
    edited = copy.deepcopy(new_value)
    edited["rules"][1]["module_prize"] = 999
    restored2, rb2 = interaction_rule_migration.plan_account_rollback(7, edited, {"rules": backup})
    assert restored2["rules"][1]["module_prize"] == 999
    assert restored2["rules"][2] == stored["rules"][2]
    assert any(item["rule_id"] == "kw-default" for item in rb2.skipped)


def test_math10_migration_skips_legacy_single_rule_and_noop() -> None:
    new_value, backup, report = interaction_rule_migration.plan_account_migration(1, {"action": "math10"})
    assert new_value is None and backup == {}
    assert report.skipped and report.skipped[0]["rule_id"] == "legacy"
    assert interaction_rule_migration.plan_account_migration(1, {"rules": [{"action": "module"}]})[0] is None


def test_math10_migration_guard(installed) -> None:
    assert interaction_rule_migration.compat_entry_conflict() is None
    _write_plugin(installed, "math10", {"interaction_entries": [{"key": "start_math10"}]})
    assert "start_math10" in (interaction_rule_migration.compat_entry_conflict() or "")


def test_math10_migration_uses_declared_default_keywords() -> None:
    migrated = interaction_rule_migration.migrate_legacy_math10_rule(
        {"action": "math10", "trigger_mode": "keyword"}, default_start_keywords=["开算数"]
    )
    assert migrated["module_start_keywords"] == ["开算数"]


def test_lint_rule_editor_and_provides() -> None:
    from app.worker.plugins.plugin_ui import lint_ui_declaration

    ok = {
        "interaction_entries": [{"key": "start"}],
        "provides": ["ai.image_backend"],
        "ui": {"rule_editor": {"entries": {"start": {"schema": {"type": "object", "properties": {"n": {"type": "integer"}}}}}}},
    }
    assert lint_ui_declaration(ok) == []
    bad = {
        "interaction_entries": [{"key": "start"}],
        "provides": ["ai.unknown_point"],
        "ui": {"rule_editor": {"entries": {"other": {"schema": {"type": "object"}}}}},
    }
    warnings = lint_ui_declaration(bad)
    assert any("ai.unknown_point" in item for item in warnings)
    assert any("ui.rule_editor.entries.other 不是" in item for item in warnings)
    assert any("ui.rule_editor.entries.other.schema" in item for item in warnings)
    # 旧插件没有新字段：不产生新警告
    assert lint_ui_declaration({"interaction_entries": [{"key": "start"}]}) == []
