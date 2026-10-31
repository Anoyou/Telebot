"""兼容层契约测试：旧插件在新服务器上照旧运行（第 21 轮 P5 准备，Plugin API 1.0 冻结计划）。

docs/PLUGIN-API-1.0-PLAN.md 列出了宿主为旧插件保留的全部兼容层（C01 … C20）。本文件做三件事：

1. :data:`COMPAT_LAYER` 是那份清单的机器可读版本：每项写明守护它的测试（已有测试 + 本文件新增）。
   ``test_guard_tests_still_exist`` 确认这些测试函数没有被删掉或改名——兼容层被悄悄删除时，
   它的守护测试通常也会一起消失，这一条能把这种情况拦下来；
2. ``test_plan_doc_lists_every_item`` 确认文档与清单一致（新增 / 删除兼容层必须同时改文档）；
3. 其余测试直接按“旧插件的写法”调用宿主，断言结果与引入新接口之前一致。

兼容层**暂时保留**（2026-10-31 项目负责人决定）：本文件只增加守护，不删除、不标记任何兼容层。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import plugin_compat
from app.worker.plugins import capabilities as caps_mod
from app.worker.plugins import loader as loader_mod
from app.worker.plugins.base import Plugin, PluginContext
from app.worker.plugins.capabilities import (
    CAPABILITIES,
    FACADE_BUFFERED,
    FACADE_ENTRY,
    FACADE_LIVE,
    PluginCapabilities,
)
from app.worker.plugins.deprecations import DEPRECATION_INDEX
from app.worker.plugins.versioning import (
    PLUGIN_API_VERSION,
    PLUGIN_CONTRACT_VERSION,
    plugin_api_satisfies,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_DIR = Path(__file__).resolve().parent
PLAN_DOC = REPO_ROOT / "docs" / "PLUGIN-API-1.0-PLAN.md"
FROZEN = REPO_ROOT / "schemas" / "plugin-api-contract-1.0.frozen.json"
THIS = "test_plugin_compat_layer.py"

# ─────────────────────────────────────────────────────
# 兼容层清单（与 docs/PLUGIN-API-1.0-PLAN.md 第 3 节一一对应）
# ─────────────────────────────────────────────────────
COMPAT_LAYER: dict[str, dict[str, Any]] = {
    "C01": {
        "name": "ctx.messages 旧能力属性（rich_table_compact 等 6 个）",
        "guards": [
            "test_plugin_platform_p0.py::test_legacy_flags_are_generated_from_single_registry",
            "test_plugin_platform_p0.py::test_every_legacy_attr_has_registry_entry",
            f"{THIS}::test_c01_legacy_attrs_match_frozen_baseline",
        ],
    },
    "C02": {
        "name": "on_interaction 交互入口迁移桥",
        "guards": [
            "test_installed_interaction_plugin_contracts.py::test_guess_number_on_interaction_uses_session_data_and_payout",
            f"{THIS}::test_c02_on_interaction_only_plugin_still_called",
        ],
    },
    "C03": {
        "name": "min_telebot_version 旧字段",
        "guards": [
            "test_plugin_loader.py::test_manifest_min_telebot_version_kept_as_legacy_alias",
            f"{THIS}::test_c03_min_telebot_version_still_gates_loading",
        ],
    },
    "C04": {
        "name": "ctx.ai tag= / tags= 别名",
        "guards": ["test_plugin_platform_p0.py::test_ai_tag_alias_is_registered_as_deprecated"],
    },
    "C05": {
        "name": "动作 send_via 旧 notice 通道（只保留失败提示与计数）",
        "guards": ["test_account_bot.py::test_deprecated_notice_channels_record_failed_action_reason"],
    },
    "C06": {
        "name": "按钮直接写 callback_data（按会话范围投递）",
        "guards": ["test_plugin_platform_p1.py::test_raw_callback_data_is_registered_deprecation"],
    },
    "C07": {
        "name": "Event Bus payload 的 owner_user_ids / admin_user_ids",
        "guards": [f"{THIS}::test_c07_event_bus_payload_keeps_admin_ids"],
    },
    "C08": {
        "name": "兜底入口表（未声明 entry_policy / legacy_entry_keys 的旧版游戏插件）",
        "guards": [
            "test_plugin_platform_p4b.py::test_old_plugin_without_declaration_keeps_fallback_table",
            "test_plugin_entries_r17.py::test_old_plugins_still_need_fallback_table",
            f"{THIS}::test_c08_fallback_entry_table_rows_unchanged",
        ],
    },
    "C09": {
        "name": "交互规则旧动作 action=\"math10\" 与主进程算数题兜底",
        "guards": [
            "test_plugin_platform_p4b.py::test_math10_migration_skips_legacy_single_rule_and_noop",
            f"{THIS}::test_c09_legacy_math10_rule_action",
        ],
    },
    "C10": {
        "name": "codex_image 旧模型名改写（gpt-5.4 → gpt-5.5）",
        "guards": [
            "test_plugin_platform_p3.py::test_plugin_without_validate_keeps_legacy_rename",
            f"{THIS}::test_c10_codex_image_model_rename",
        ],
    },
    "C11": {
        "name": "chatgpt_image 多 Token 旧配置（等价 x-secret-list）",
        "guards": [
            "test_plugin_platform_p3.py::test_chatgpt_image_legacy_compat_matches_old_behaviour",
            f"{THIS}::test_c11_chatgpt_image_secret_list_fallback",
        ],
    },
    "C12": {
        "name": "规则试运行旧协议 _dry_run_match",
        "guards": [f"{THIS}::test_c12_legacy_dry_run_protocol"],
    },
    "C13": {
        "name": ",ai 图片后端 image_backend=\"codex_image\" 与无声明时回退",
        "guards": [
            "test_plugin_platform_p4b.py::test_select_image_backend_plugin",
            "test_plugin_platform_p4b.py::test_command_schema_image_backend_defaults_and_compat",
            f"{THIS}::test_c13_image_backend_legacy_paths",
        ],
    },
    "C14": {
        "name": "动作 compact_tables: true（render_profile 的旧写法）",
        "guards": [f"{THIS}::test_c14_compact_tables_absorbed"],
    },
    "C15": {
        "name": "UserBot 旧分发 on_message（message_channels）",
        "guards": [
            "test_plugin_loader.py::test_userbot_event_bus_missing_entry_key_keeps_legacy_on_message",
            "test_plugin_loader.py::test_userbot_event_bus_unmatched_subscription_keeps_legacy_on_message",
        ],
    },
    "C16": {
        "name": "直通入口 on_direct_message 旧返回值 True / {\"consume\": ...}",
        "guards": ["test_plugin_loader.py::test_direct_passthrough_priority_and_declarative_consume"],
    },
    "C17": {
        "name": "已迁出 Core 的可选插件 key 与旧 installed 目录 / 旧 account_feature 行",
        "guards": [
            "test_plugin_platform_p0.py::test_non_core_plugin_key_list_has_single_source",
            f"{THIS}::test_c17_non_core_optional_keys_unchanged",
        ],
    },
    "C18": {
        "name": "账号级旧 global 配置字段 / Feature.manifest[\"global_config\"] 回读",
        "guards": [
            "test_plugin_loader.py::test_merge_plugin_config_uses_legacy_account_global_field_when_global_empty",
            "test_plugin_loader.py::test_merge_plugin_config_prefers_saved_global_over_legacy_account_global_field",
        ],
    },
    "C19": {
        "name": "旧版免签名插件（plugin_allow_legacy_unsigned_plugins）",
        "guards": [
            "test_plugin_loader.py::test_authorize_installed_plugin_allows_legacy_unsigned_when_enabled",
            "test_plugin_loader.py::test_authorize_installed_plugin_rejects_legacy_unsigned_when_disabled",
        ],
    },
    "C20": {
        "name": "宿主内置转账通知解析器（与 payment_sources 插件双跑，以宿主为准）",
        "guards": ["test_payment_sources.py::test_golden_host_legacy_parser_unchanged"],
    },
}


def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }


def test_guard_tests_still_exist() -> None:
    cache: dict[str, set[str]] = {}
    missing: list[str] = []
    for item_id, item in COMPAT_LAYER.items():
        assert item["guards"], f"{item_id} 没有守护测试"
        for ref in item["guards"]:
            file_name, func = ref.split("::")
            if file_name not in cache:
                path = TESTS_DIR / file_name
                cache[file_name] = _test_functions(path) if path.is_file() else set()
            if func not in cache[file_name]:
                missing.append(f"{item_id}: {ref}")
    assert not missing, "兼容层守护测试被删除或改名：" + "；".join(missing)


def test_plan_doc_lists_every_item() -> None:
    text = PLAN_DOC.read_text(encoding="utf-8")
    for item_id in COMPAT_LAYER:
        assert f"| {item_id} |" in text, f"docs/PLUGIN-API-1.0-PLAN.md 缺少兼容层 {item_id}"
    # 文档里出现的编号不能多于清单（删除兼容层要同时改测试清单）
    doc_ids = {f"C{n:02d}" for n in range(1, 100) if f"| C{n:02d} |" in text}
    assert doc_ids == set(COMPAT_LAYER)


def test_registered_deprecations_are_all_still_present() -> None:
    """弃用登记只增不减：登记 ≠ 删除，旧插件调用这些接口仍然可用。"""

    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    assert set(frozen["deprecations"]) <= set(DEPRECATION_INDEX)


# ─────────────────────────────────────────────────────
# 契约版本探测（Plugin API 1.7）
# ─────────────────────────────────────────────────────
def test_contract_version_exposed_on_ctx_capabilities() -> None:
    caps = PluginCapabilities()
    assert caps.contract_version == PLUGIN_CONTRACT_VERSION == "1.0"
    assert caps.has("runtime.contract_version")
    assert "runtime.contract_version" in caps.names()


def test_plugin_side_fallback_when_host_has_no_contract_version() -> None:
    """插件推荐写法：旧宿主（没有该属性 / 没有 ctx.capabilities）回退为 None。"""

    def probe(ctx: Any) -> str | None:
        caps = getattr(ctx, "capabilities", None)
        return getattr(caps, "contract_version", None) if caps is not None else None

    assert probe(SimpleNamespace()) is None  # 0.107 之前：没有 ctx.capabilities
    assert probe(SimpleNamespace(capabilities=SimpleNamespace(plugin_api="1.6"))) is None  # 0.107–0.115.0-beta.1
    assert probe(SimpleNamespace(capabilities=PluginCapabilities())) == "1.0"


@pytest.mark.asyncio
async def test_capabilities_api_returns_contract_version() -> None:
    from app.api.plugins import get_plugin_capabilities

    resp = await get_plugin_capabilities(_user=None)  # type: ignore[arg-type]
    assert resp.plugin_api_version == PLUGIN_API_VERSION
    assert resp.contract_version == "1.0"
    assert resp.contract_baseline_plugin_api == "1.6"
    assert any(item.name == "runtime.contract_version" for item in resp.capabilities)


def test_plugin_api_bump_keeps_published_ranges() -> None:
    # 已发布插件常见的 plugin_api 写法在 1.7 宿主上照旧成立；min_telepilot_version 不受影响。
    for spec in ("^1.0", ">=1.3", "^1.4", "^1.6", "1.x", ">=1.0,<2"):
        assert plugin_api_satisfies(spec, PLUGIN_API_VERSION)


# ─────────────────────────────────────────────────────
# 各兼容层的直接守护
# ─────────────────────────────────────────────────────
def test_c01_legacy_attrs_match_frozen_baseline() -> None:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    baseline: dict[str, list[str]] = frozen["capabilities"]["legacy_attrs_true"]
    classes = {
        FACADE_BUFFERED: loader_mod.BufferedMessageOps,
        FACADE_LIVE: loader_mod._LiveMessageOps,
        FACADE_ENTRY: loader_mod._InteractionEntryMessageOps,
    }
    for facade, attrs in baseline.items():
        cls = classes[facade]
        for attr in attrs:
            # 旧插件写法：getattr(ctx.messages, "rich_table_compact", False) is True
            assert getattr(cls, attr, False) is True, f"{facade}.{attr}"
    # 6 个旧属性都还在注册表里（只增不减）
    legacy = {item.legacy_attr for item in CAPABILITIES if item.legacy_attr}
    assert {
        "inline_rich_edit",
        "live_inline_edit",
        "rich_table_compact",
        "rich_table_default_style",
        "guest_reply_markup",
        "streaming_edit",
    } <= legacy
    assert caps_mod.legacy_flags_for(FACADE_ENTRY)


@pytest.mark.asyncio
async def test_c02_on_interaction_only_plugin_still_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """只实现 on_interaction 的旧插件：交互入口仍调用它，并登记一次弃用计数（不拒绝、不改行为）。"""

    calls: list[tuple[str, dict[str, Any]]] = []
    noted: list[str] = []
    monkeypatch.setattr(loader_mod, "note_deprecated", lambda dep_id, **_kw: noted.append(dep_id))

    class _OldInteractionPlugin(Plugin):
        key = "_test_old_interaction"
        display_name = "旧交互插件"

        async def on_interaction(self, ctx: PluginContext, entry_key: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
            calls.append((entry_key, payload))
            return [{"type": "send_message", "text": "旧入口照旧"}]

    state = loader_mod._AccountState(account_id=931)
    state.instances["_test_old_interaction"] = _OldInteractionPlugin()
    state.contexts["_test_old_interaction"] = PluginContext(account_id=931, feature_key="_test_old_interaction")
    loader_mod._STATES[931] = state
    try:
        actions = await loader_mod.invoke_interaction_entry(
            931,
            plugin_key="_test_old_interaction",
            entry_key="start_game",
            payload={"trace_id": "evt-old-interaction", "text": "开始", "trigger": {"dispatch_mode": "entry"}},
        )
    finally:
        loader_mod._STATES.pop(931, None)

    assert [entry for entry, _ in calls] == ["start_game"]
    assert calls[0][1]["text"] == "开始"
    assert any(action.get("text") == "旧入口照旧" for action in actions)
    assert noted == ["hook.on_interaction"]


def test_c03_min_telebot_version_still_gates_loading() -> None:
    def manifest(**kw: Any) -> SimpleNamespace:
        base = {"min_telepilot_version": None, "min_telebot_version": None, "plugin_api": None, "requires_features": []}
        base.update(kw)
        return SimpleNamespace(**base)

    ok, reason = loader_mod._manifest_compatible(manifest(min_telebot_version="0.15.0"))
    assert ok, reason
    ok, reason = loader_mod._manifest_compatible(manifest(min_telebot_version="99.0.0"))
    assert not ok and "99.0.0" in str(reason)
    # 两个都写时以新字段为准
    ok, _ = loader_mod._manifest_compatible(manifest(min_telepilot_version="0.15.0", min_telebot_version="99.0.0"))
    assert ok


def test_c07_event_bus_payload_keeps_admin_ids() -> None:
    from app.services import account_bot_runtime

    payload = account_bot_runtime._attach_event_bus_admin_ids(
        {"text": "hi"}, {"owner_user_ids": [11], "admin_user_ids": [11, 22]}
    )
    assert payload["owner_user_ids"] == [11]
    assert payload["admin_user_ids"] == [11, 22]
    # 已有同名字段不覆盖（规则链路早已带这两个字段）
    kept = account_bot_runtime._attach_event_bus_admin_ids(
        {"owner_user_ids": [1], "admin_user_ids": [1, 2]}, {"owner_user_ids": [11]}
    )
    assert kept["owner_user_ids"] == [1] and kept["admin_user_ids"] == [1, 2]


def test_c08_fallback_entry_table_rows_unchanged() -> None:
    assert plugin_compat.FALLBACK_CHAT_SESSION_MODULE_ENTRIES == {
        ("dice_grid_hunt", "start_dice_grid_hunt"),
        ("dice_grid_hunt", "answer_dice_grid_hunt"),
        ("game24", "start_paid_game"),
        ("math10", "start_math10"),
        ("guess_number", "start_game"),
    }
    assert plugin_compat.FALLBACK_NO_PRIZE_MODULE_ENTRIES == {("pt_promote", "promote_torrent")}
    assert plugin_compat.FALLBACK_SOLO_OWNER_MODULE_ENTRIES == {("blackjack", "start_blackjack")}
    assert plugin_compat.fallback_entry_session_scope("math10", "start_math10") == "chat"
    assert plugin_compat.fallback_entry_participant_policy("blackjack", "start_blackjack") == "solo_owner"
    assert plugin_compat.fallback_entry_has_field("pt_promote", "promote_torrent", "prize") is False
    assert plugin_compat.fallback_entry_keys("dice_grid_hunt") == ["answer_dice_grid_hunt", "start_dice_grid_hunt"]


def test_c09_legacy_math10_rule_action() -> None:
    assert plugin_compat.is_legacy_math10_action("math10")
    assert plugin_compat.is_legacy_math10_action(" math10 ")
    assert not plugin_compat.is_legacy_math10_action("module")
    assert {"notice", "math10", "module"} == set(plugin_compat.RULE_ACTIONS)
    assert plugin_compat.LEGACY_RULE_ACTION_LABELS["math10"] == "算数题"
    assert plugin_compat.has_main_process_fallback("math10")
    assert "发十以内算数" in plugin_compat.DEFAULT_MATH10_START_KEYWORDS


def test_c10_codex_image_model_rename() -> None:
    out = plugin_compat.normalize_legacy_feature_config("codex_image", {"model": "gpt-5.4", "x": 1})
    assert out == {"model": "gpt-5.5", "x": 1}
    same = {"model": "gpt-5.4"}
    assert plugin_compat.normalize_legacy_feature_config("other_plugin", same) is same


def test_c11_chatgpt_image_secret_list_fallback() -> None:
    spec = plugin_compat.legacy_secret_list_fields("chatgpt_image", {"properties": {"tokens": {"type": "array"}}})
    assert spec == {"tokens": {"secret_field": "token", "note_field": "note", "id_field": "token_id", "legacy_field": "token"}}
    # 新版本自己声明了 x-secret-list：兜底不再生效
    declared = {"properties": {"tokens": {"type": "array", "x-secret-list": {"secret_field": "token"}}}}
    assert plugin_compat.legacy_secret_list_fields("chatgpt_image", declared) == {}
    assert plugin_compat.uses_multi_token_config("chatgpt_image")


def test_c12_legacy_dry_run_protocol() -> None:
    sample = {"message": "你好", "chat_id": -100, "chat_type": "group"}
    assert plugin_compat.legacy_dry_run_args("auto_reply", {}, sample) == ({}, "你好", "group", -100)
    assert plugin_compat.legacy_dry_run_args("autorepeat", {}, sample) == ({}, "你好", -100)
    logs = plugin_compat.legacy_dry_run_logs("auto_reply", {"scope": "all"}, sample, matched=True, output=None)
    assert logs[0] == {"step": "scope", "msg": "会话类型：group"}
    assert logs[-1] == {"step": "result", "msg": "命中"}
    codex = plugin_compat.legacy_dry_run_logs("codex_image", {}, sample, matched=False, output=None)
    assert codex[-1] == {"step": "result", "msg": "未命中（缺 access_token）"}


def test_c13_image_backend_legacy_paths() -> None:
    from app.worker.plugins.extension_points import select_image_backend_plugin

    # 旧模板保存的 "codex_image"：固定走 codex_image
    def pick(backend: str, providers: list[str]) -> tuple[str, str]:
        return select_image_backend_plugin(
            image_backend=backend, image_plugin_key=None, providers=providers, legacy_default="codex_image"
        )

    assert pick("codex_image", ["new_backend"]) == ("codex_image", "legacy")
    # 没有任何插件声明 ai.image_backend：回退 codex_image（旧插件 + 新服务器与 0.110 一致）
    assert pick("plugin", []) == ("codex_image", "fallback")
    assert plugin_compat.IMAGE_BACKENDS == ("plugin", "codex_image", "llm")


def test_c14_compact_tables_absorbed() -> None:
    from app.services.render_profile import absorb_legacy_compact

    action: dict[str, Any] = {"type": "send_rich", "compact_tables": True}
    absorb_legacy_compact(action)
    assert "compact_tables" not in action
    assert action["render_profile"]["table"]["compact"] is True
    off: dict[str, Any] = {"type": "send_rich", "compact_tables": "yes"}
    absorb_legacy_compact(off)
    assert off == {"type": "send_rich"}  # 非 True 的值等于没写，且永远不发给 Telegram


def test_c17_non_core_optional_keys_unchanged() -> None:
    assert plugin_compat.NON_CORE_OPTIONAL_PLUGIN_KEYS == {
        "auto_reply",
        "autorepeat",
        "chatgpt_image",
        "codex_image",
        "game24",
        "math10",
    }
