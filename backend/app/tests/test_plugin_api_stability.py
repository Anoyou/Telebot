"""Plugin API 1.0 冻结范围（第 22 轮拍板 1B 2B 3B 4B 5A 6B 7A）的守护测试。

- 冻结范围基线 schemas/plugin-api-contract-1.0-scope.frozen.json ⊆ 当前快照 stability 段；检测器能识别破坏；
- 每个「稳定」条目宿主今天确实还在提供：动作字段仍被执行器读取、事件信封仍产出标准栏目、ctx.client 方法仍在
  权限白名单、Manifest 参数仍存在、AI 错误类型继承关系不变、插件包必需文件与 Ed25519 签名格式不变、
  registry.json 目录版本 1 的字段全部被宿主读取；
- manifest.py 与 plugin.json 一致性检查：示例插件全部一致，矛盾能被指出；
- 文档同步：PLUGIN-CONTRACT.md / PLUGIN-API-1.0-PLAN.md 写明每块的稳定 / 实验划分。

只登记、不改行为：兼容层 20 项全部保留，任何插件的 min_telepilot_version 都不需要变化。
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import event_bus, plugin_install_service
from app.services import plugin_market_service as market
from app.worker.plugins import ai_facade, contract, sandbox, stability
from app.worker.plugins.manifest import Manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_APP = REPO_ROOT / "backend" / "app"
DOCS = REPO_ROOT / "docs"
EXAMPLES = sorted((REPO_ROOT / "examples" / "plugins").glob("*/plugin.json"))


@pytest.fixture(scope="module")
def current() -> dict:
    return contract.build_contract()


@pytest.fixture(scope="module")
def scope_baseline() -> dict:
    return contract.load_json(contract.SCOPE_BASELINE_PATH)


def _string_constants(*relative: str) -> set[str]:
    found: set[str] = set()
    for rel in relative:
        tree = ast.parse((BACKEND_APP / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


# ── 冻结范围基线 ──────────────────────────────────────


def test_scope_baseline_is_subset_of_current(current: dict) -> None:
    problems = list(contract.iter_scope_problems(current))
    assert problems == [], "Plugin API 1.0 冻结范围只允许新增：\n" + "\n".join(problems)


def test_scope_baseline_only_holds_stable_parts(scope_baseline: dict) -> None:
    text = json.dumps(scope_baseline, ensure_ascii=False)
    assert "webhook_envelope" not in scope_baseline  # 整块实验
    assert '"experimental"' not in text and '"compat"' not in text
    assert scope_baseline["client_methods"]["level"] == stability.ADVANCED_COMPAT
    for key in ("action_fields", "event_payload", "manifest_py", "ai_errors", "remote_package", "registry"):
        assert scope_baseline[key]["level"] == stability.STABLE, key


def test_snapshot_marks_experimental_parts(current: dict) -> None:
    section = current["stability"]
    assert section["webhook_envelope"]["level"] == stability.EXPERIMENTAL
    assert "send_via" in section["action_fields"]["experimental"]
    assert "send_via_options" in section["action_fields"]["experimental"]
    assert "rich_message" in section["action_fields"]["experimental"]
    assert section["event_payload"]["compat"] == {"owner_user_ids": "C07", "admin_user_ids": "C07"}
    assert section["manifest_py"]["compat"] == {"min_telebot_version": "C03"}
    assert set(section["manifest_py"]["experimental"]).isdisjoint(stability.MANIFEST_PARAMS_STABLE)


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda s: s["action_fields"]["stable"]["send_message"].remove("reply_markup"), "send_message"),
        (lambda s: s["action_fields"]["stable"].pop("payout"), "payout"),
        (lambda s: s["event_payload"]["stable"]["trigger"].remove("entry_key"), "trigger"),
        (lambda s: s["client_methods"]["stable"]["moderate_chat"].remove("mute_user"), "moderate_chat"),
        (lambda s: s["client_methods"]["always_allowed"].remove("get_me"), "always_allowed"),
        (lambda s: s["manifest_py"]["stable"]["interaction_entries"].update(present=False), "interaction_entries"),
        (lambda s: s["manifest_py"]["stable"]["version"].update(has_default=False), "version"),
        (lambda s: s["manifest_py"]["stable"]["display_name"].update(position=2), "display_name"),
        (lambda s: s["ai_errors"]["stable"]["AICapabilityError"]["ancestors"].remove("AIUnavailableError"), "AICapabilityError"),
        (lambda s: s["remote_package"]["required_files"].remove("plugin.json"), "required_files"),
        (lambda s: s["remote_package"]["signature"].update(algorithm="RSA"), "algorithm"),
        (lambda s: s["registry"].update(schema_version=2), "schema_version"),
        (lambda s: s["registry"]["entry_fields"].remove("min_telepilot_version"), "entry_fields"),
        (lambda s: s["action_fields"].update(level="experimental"), "level"),
    ],
)
def test_scope_detector_catches_breaking_changes(current: dict, scope_baseline: dict, mutate, needle: str) -> None:
    broken = copy.deepcopy(current)
    mutate(broken["stability"])
    problems = list(contract.iter_scope_problems(broken))
    assert problems, "破坏性改动未被识别"
    assert any(needle in item and item.startswith("$.stability") for item in problems), problems


def test_scope_detector_allows_additions_and_experimental_changes(current: dict) -> None:
    grown = copy.deepcopy(current)
    section = grown["stability"]
    section["action_fields"]["stable"]["send_message"].append("new_field")
    section["action_fields"]["stable"]["send_photo"] = ["photo_base64"]
    section["action_fields"]["experimental"] = []  # 实验部分可以调整
    section["event_payload"]["experimental"].append("new_block")
    section["webhook_envelope"] = {"level": "experimental", "experimental": ["v2"]}
    section["registry"]["entry_fields"].append("homepage")
    section["manifest_py"]["stable"]["new_param"] = {"present": True, "has_default": True}
    assert list(contract.iter_scope_problems(grown)) == []


# ── 1. 标准动作字段：宿主仍在读取 ──────────────────────


def test_stable_action_types_are_canonical(current: dict) -> None:
    assert set(stability.ACTION_FIELDS_STABLE) <= set(current["actions"])


@pytest.mark.parametrize("field", sorted({f for fields in stability.ACTION_FIELDS_STABLE.values() for f in fields}))
def test_stable_action_field_is_read_by_executors(field: str) -> None:
    readers = _string_constants(
        "services/interaction/delivery.py",
        "services/interaction/userbot_actions.py",
        "services/interaction/contracts.py",
    )
    assert field in readers, f"交互执行器不再读取稳定动作字段 {field!r}"


def test_payout_fields_keep_platform_settlement() -> None:
    # 只冻结字段名：结算仍由宿主执行（“金额”是群内游戏币），payout 依旧走宿主的发奖补偿键。
    source = (BACKEND_APP / "services" / "interaction" / "delivery.py").read_text(encoding="utf-8")
    assert "payout_compensation_service.ensure_payout_key" in source


# ── 2. Event Bus 信封：宿主仍产出标准栏目 ──────────────


def _bot_events() -> list[dict]:
    message = {
        "update_id": 5,
        "message": {
            "message_id": 9,
            "date": 1,
            "chat": {"id": -100, "type": "supergroup", "title": "g"},
            "from": {"id": 7, "first_name": "A", "username": "a"},
            "text": "hi",
            "reply_to_message": {"message_id": 8, "from": {"id": 6, "first_name": "B"}, "text": "x"},
        },
    }
    callback = {
        "update_id": 6,
        "callback_query": {
            "id": "c1",
            "data": "d",
            "from": {"id": 7, "first_name": "A", "username": "a"},
            "message": {"message_id": 9, "chat": {"id": -100, "type": "supergroup"}, "date": 1, "text": "t"},
        },
    }
    return [event_bus.normalize_bot_update(1, message), event_bus.normalize_bot_update(1, callback)]


def test_event_envelope_keeps_stable_blocks() -> None:
    message_event, callback_event = _bot_events()
    for block in stability.EVENT_PAYLOAD_STABLE:
        assert block in message_event, f"事件信封缺少稳定栏目 {block}"
    for block in ("message", "chat", "sender", "actor", "source_actor", "player"):
        expected = set(stability.EVENT_PAYLOAD_STABLE[block])
        assert expected <= set(message_event[block]), (block, expected - set(message_event[block]))
    source_fields = set(stability.EVENT_PAYLOAD_STABLE["source"])
    assert source_fields - {"callback_query_id", "callback_data"} <= set(message_event["source"])
    assert source_fields <= set(callback_event["source"])
    assert set(stability.EVENT_PAYLOAD_STABLE["reply_to"]) <= set(message_event["reply_to"])


@pytest.mark.parametrize("block", ["payment", "trigger", "session"])
def test_event_runtime_blocks_still_written(block: str) -> None:
    writers = _string_constants("services/account_bot_runtime.py", "services/payment_sources.py", "services/event_bus.py")
    missing = [f for f in stability.EVENT_PAYLOAD_STABLE[block] if f not in writers]
    assert missing == [], f"交互运行时不再写入 {block} 的稳定字段 {missing}"


def test_event_compat_fields_are_not_stable() -> None:
    stable_names = {f for fields in stability.EVENT_PAYLOAD_STABLE.values() for f in fields} | set(stability.EVENT_PAYLOAD_STABLE)
    assert stable_names.isdisjoint(stability.EVENT_PAYLOAD_COMPAT)


# ── 3. ctx.client：方法名与权限对应 ────────────────────


def test_client_methods_match_sandbox_whitelist() -> None:
    for perm, methods in stability.CLIENT_PERMISSION_METHODS_STABLE.items():
        assert set(methods) <= set(sandbox.ALLOWED_API.get(perm, ())), perm
    assert set(stability.CLIENT_ALWAYS_ALLOWED_STABLE) <= set(sandbox._ALWAYS_ALLOWED)


# ── 4. Manifest(...) 构造参数 ─────────────────────────


def test_manifest_stable_params_exist(current: dict) -> None:
    stable = current["stability"]["manifest_py"]["stable"]
    assert all(item["present"] for item in stable.values()), stable
    assert stable["key"]["position"] == 0 and stable["display_name"]["position"] == 1
    assert not stable["key"]["has_default"] and not stable["display_name"]["has_default"]
    # 除 key / display_name 外都有默认值：老 manifest.py 不写也能构造。
    assert all(item["has_default"] for name, item in stable.items() if name not in {"key", "display_name"})


def test_manifest_parity_fields_map_to_plugin_json(current: dict) -> None:
    # 安装 Schema 顶层字段 + 详细契约 Schema 顶层字段（allowed_hosts 只在后者）。
    install_fields = set(current["manifest"]["install_fields"]) | {k for k in current["manifest"]["fields"] if "." not in k and "[" not in k}
    for attr, json_key in {**stability.MANIFEST_PARITY_FIELDS, **stability.MANIFEST_PARITY_WARN_FIELDS}.items():
        assert attr in stability.MANIFEST_PARAMS_STABLE, attr
        assert json_key in install_fields, json_key


def _load_manifest(path: Path) -> Manifest:
    spec = importlib.util.spec_from_file_location(f"r22_parity_{path.parent.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MANIFEST


@pytest.mark.parametrize("plugin_json", EXAMPLES, ids=[p.parent.name for p in EXAMPLES])
def test_examples_manifest_py_matches_plugin_json(plugin_json: Path) -> None:
    raw = json.loads(plugin_json.read_text(encoding="utf-8"))
    errors, _warnings = stability.manifest_parity_problems(raw, _load_manifest(plugin_json.parent / "manifest.py"))
    assert errors == []


def test_manifest_parity_reports_conflicts_and_tolerates_omissions() -> None:
    raw = {"name": "demo", "display_name": "演示", "version": "1.0.0", "permissions": ["send_message"], "author": "A", "reads": ["ai.models@1"]}
    same = Manifest(key="demo", display_name="演示", version="1.0.0", permissions=["send_message"], author="A")
    assert stability.manifest_parity_problems(raw, same) == ([], [])  # reads 只写在 plugin.json：不算矛盾
    wrong = Manifest(key="demo", display_name="别名", version="1.0.1", permissions=["send_message"], author="B")
    errors, warnings = stability.manifest_parity_problems(raw, wrong)
    assert any("display_name" in e for e in errors) and any("version" in e for e in errors)
    assert warnings and "author" in warnings[0]


# ── 5. AI 错误类型 ───────────────────────────────────


def test_ai_error_hierarchy() -> None:
    for name, base in stability.AI_ERRORS_STABLE.items():
        cls = getattr(ai_facade, name)
        assert issubclass(cls, getattr(ai_facade, base)), name


# ── 6. 插件包与签名 ───────────────────────────────────


def test_package_required_files() -> None:
    assert set(stability.PACKAGE_REQUIRED_FILES) <= set(plugin_install_service.REQUIRED_FILES)


def test_package_signature_is_detached_ed25519_over_zip_bytes() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    payload = b"PK\x03\x04 fake zip bytes"
    signature = key.sign(payload)
    assert plugin_install_service.verify_signature(payload, signature, pem) is True
    assert plugin_install_service.verify_signature(payload + b"x", signature, pem) is False
    assert plugin_install_service.verify_signature(payload, None, pem) is None


def test_webhook_envelope_is_experimental_and_still_delivered() -> None:
    payload = event_bus.normalize_webhook_event(1, hook_key="orders", body={"a": 1})
    for key in stability.WEBHOOK_ENVELOPE_EXPERIMENTAL:
        assert key in payload


# ── 7. registry.json 目录版本 1 ───────────────────────


def test_registry_v1_entry_fields_are_read() -> None:
    raw_entry = {
        "key": "demo",
        "name": {"zh": "演示", "en": "Demo"},
        "kind": "utility",
        "icon": "🧰",
        "summary": "s",
        "version": "1.0.0",
        "author": "a",
        "min_telepilot_version": "0.110.0",
        "plugin_api": "^1.3",
        "permissions": ["send_message"],
        "reads": [],
        "tags": ["t"],
        "path": "demo",
        "channels": {"stable": "main"},
        "repository": "https://github.com/me/p.git",
        "trust": "community",
    }
    assert set(raw_entry) == set(stability.REGISTRY_V1_ENTRY_FIELDS)
    data = {"schema_version": 1, "name": "x", "trust": "community", "repository": "https://github.com/me/p.git", "homepage": "https://x", "plugins": [raw_entry]}
    assert set(data) == set(stability.REGISTRY_V1_TOP_FIELDS)
    entries, _ = market.parse_registry(data, source={"trust": "community"})
    assert len(entries) == 1
    assert set(stability.REGISTRY_V1_ENTRY_FIELDS) <= set(entries[0])
    assert entries[0]["min_telepilot_version"] == "0.110.0" and entries[0]["plugin_api"] == "^1.3"


def test_registry_parse_ignores_schema_version_unchanged() -> None:
    # 冻结只登记格式，不改行为：parse_registry 现在不按 schema_version 拒绝索引。
    data = {"schema_version": 2, "repository": "https://github.com/me/p.git", "plugins": [{"key": "demo", "version": "1.0.0"}]}
    entries, _ = market.parse_registry(data, source={"trust": "unreviewed"})
    assert [e["key"] for e in entries] == ["demo"]


def test_builtin_registry_is_v1() -> None:
    data = json.loads(market.BUILTIN_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert data["schema_version"] == stability.REGISTRY_SCHEMA_VERSION == market.REGISTRY_SCHEMA_VERSION == 1
    assert set(data) <= set(stability.REGISTRY_V1_TOP_FIELDS)
    allowed = set(stability.REGISTRY_V1_ENTRY_FIELDS) | set(stability.REGISTRY_V1_ADDITIVE_ENTRY_FIELDS)
    for entry in data["plugins"]:
        assert set(entry) <= allowed, entry.get("key")
        assert isinstance(entry.get("summary"), str), entry.get("key")  # 旧服务器按字符串读 summary


def test_registry_additive_fields_are_disjoint_and_read() -> None:
    assert set(stability.REGISTRY_V1_ADDITIVE_ENTRY_FIELDS).isdisjoint(stability.REGISTRY_V1_ENTRY_FIELDS)
    raw = {
        "key": "demo",
        "version": "1.0.0",
        "summary": "s",
        "summary_i18n": {"zh": "演示", "en": "Demo"},
        "description": "d",
        "usage": "u",
        "commands": ["demo"],
        "release_notes": "- n",
        "released_at": "2026-11-01",
        "homepage": "https://example.com/demo",
        "license": "MIT",
        "icon_url": "https://example.com/i.png",
        "screenshots": ["https://example.com/s.png"],
        "channel_versions": {"stable": "1.0.0"},
    }
    assert set(stability.REGISTRY_V1_ADDITIVE_ENTRY_FIELDS) <= set(raw)
    data = {"schema_version": 1, "repository": "https://github.com/me/p.git", "plugins": [raw]}
    entries, _ = market.parse_registry(data, source={"trust": "community"})
    for field in stability.REGISTRY_V1_ADDITIVE_ENTRY_FIELDS:
        assert entries[0][field], field


# ── 版本号：只登记范围，不动契约版本与 Plugin API ───────


def test_freeze_scope_keeps_versions(current: dict) -> None:
    assert current["contract_version"] == "1.0"
    assert current["baseline_plugin_api"] == "1.6"
    assert str(current["plugin_api_version"]).split(".")[0] == "1"


# ── 文档 ────────────────────────────────────────────


def test_docs_describe_stability_split() -> None:
    plan = (DOCS / "PLUGIN-API-1.0-PLAN.md").read_text(encoding="utf-8")
    contract_doc = (DOCS / "PLUGIN-CONTRACT.md").read_text(encoding="utf-8")
    assert "状态：已定" in plan
    for word in ("plugin-api-contract-1.0-scope.frozen.json", "send_via_options", "AIResponseTruncated", "schema_version", "Webhook"):
        assert word in plan, word
        assert word in contract_doc, word
    assert "实验" in contract_doc and "高级 / 兼容" in contract_doc
    # 兼容层只增不删：20 项都还在文档里，且没有“即将删除”的字样。
    for idx in range(1, 21):
        assert f"| C{idx:02d} |" in plan
    for line in plan.splitlines():
        if "即将删除" in line:
            assert "不" in line.split("即将删除")[0], line


def test_stability_constants_have_no_sim_namespace_leak() -> None:
    # SimpleNamespace 风格的 manifest 也能做一致性比较（插件库检查器用替身 Manifest 加载 manifest.py）。
    errors, _ = stability.manifest_parity_problems({"name": "x", "version": "1"}, SimpleNamespace(key="x", version="1"))
    assert errors == []
