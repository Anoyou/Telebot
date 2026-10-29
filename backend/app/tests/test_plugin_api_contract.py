"""插件稳定契约 1.0 的契约测试。

- 快照（schemas/plugin-api-contract.json）与运行时内省一致，详细 Schema（plugin-contract.schema.json）最新；
- 相对冻结基线（plugin-api-contract-1.0.frozen.json）没有破坏性改动，破坏检测器本身能识别各类破坏；
- 版本方案：契约版本独立于 PLUGIN_API_VERSION，旧插件的 plugin_api 范围判断不受影响；
- manifest 校验：示例 / 内置插件通过安装 Schema 与契约 Schema，典型写错被契约 Schema 拦下；
- ctx.capabilities 快照与钩子签名：基线里每项能力在同一 facade 下仍可探测，钩子签名可按基线参数调用。
"""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.worker.plugins import contract
from app.worker.plugins.base import Plugin
from app.worker.plugins.versioning import PLUGIN_API_VERSION, plugin_api_satisfies

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = sorted((REPO_ROOT / "examples" / "plugins").glob("*/plugin.json"))
BUILTINS = sorted((REPO_ROOT / "backend" / "app" / "worker" / "plugins" / "builtin").glob("*/plugin.json"))


@pytest.fixture(scope="module")
def current() -> dict:
    return contract.build_contract()


@pytest.fixture(scope="module")
def frozen() -> dict:
    return contract.load_json(contract.FROZEN_BASELINE_PATH)


@pytest.fixture(scope="module")
def contract_schema() -> Draft202012Validator:
    return Draft202012Validator(contract.build_manifest_schema())


# ── 快照与基线 ────────────────────────────────────────


def test_snapshot_file_matches_runtime(current: dict) -> None:
    on_disk = contract.CONTRACT_PATH.read_text(encoding="utf-8")
    assert on_disk == contract.dumps(current), "运行 python scripts/export-plugin-contract.py 重新生成插件契约快照"


def test_contract_schema_file_matches_runtime() -> None:
    on_disk = json.loads(contract.CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == contract.build_manifest_schema(), "运行 python scripts/export-plugin-contract.py 重新生成 plugin-contract.schema.json"


def test_no_breaking_changes_against_frozen_baseline(current: dict) -> None:
    problems = list(contract.iter_contract_problems(current))
    assert problems == [], "插件契约 1.x 只允许新增：\n" + "\n".join(problems)


def test_frozen_baseline_identity(frozen: dict) -> None:
    assert frozen["contract_version"] == "1.0"
    assert frozen["baseline_plugin_api"] == "1.6"
    # 基线覆盖契约文档列出的全部部分
    for section in (
        "hooks", "context", "facades", "capabilities", "manifest", "permissions", "ui", "bridge",
        "events", "actions", "extension_points", "host_read_resources", "deprecations",
    ):
        assert frozen.get(section), section


# ── 版本方案 ──────────────────────────────────────────


def test_contract_version_is_independent_of_plugin_api() -> None:
    assert contract.PLUGIN_CONTRACT_VERSION == "1.0"
    major, minor = (int(part) for part in PLUGIN_API_VERSION.split("."))
    base_major, base_minor = (int(part) for part in contract.CONTRACT_BASELINE_PLUGIN_API.split("."))
    # Plugin API 不回退、不跨 MAJOR：旧插件声明的范围照旧成立
    assert major == base_major == 1
    assert minor >= base_minor


@pytest.mark.parametrize("spec", ["^1.0", "^1.3", "^1.4", ">=1.0,<2", ">=1.5", "1.x", "1", "1.6", "~1", "*", ""])
def test_published_plugin_api_ranges_still_satisfied(spec: str) -> None:
    assert plugin_api_satisfies(spec) is True


@pytest.mark.parametrize("spec", ["^2.0", ">=2", "<1.0"])
def test_out_of_range_specs_still_rejected(spec: str) -> None:
    assert plugin_api_satisfies(spec) is False


# ── 破坏检测器 ────────────────────────────────────────


def _mutate(frozen: dict, fn) -> list[str]:
    cur = copy.deepcopy(frozen)
    fn(cur)
    return contract.breaking_changes(frozen, cur)


def _hook(c: dict, name: str) -> dict:
    return next(item for item in c["hooks"] if item["name"] == name)


def test_detector_accepts_additive_changes(frozen: dict) -> None:
    def additive(c: dict) -> None:
        c["hooks"].append({"name": "on_new_hook", "kind": "plugin_method", "params": []})
        _hook(c, "on_event")["params"].append({"name": "extra", "kind": "KEYWORD_ONLY", "has_default": True})
        c["capabilities"]["registry"].append({"name": "x.new", "version": 1})
        c["capabilities"]["ctx_capabilities_has"]["live"].append("x.new")
        c["manifest"]["fields"]["brand_new"] = ["string"]
        c["manifest"]["fields"]["plugin_api"] = sorted({*c["manifest"]["fields"]["plugin_api"], "object"})
        c["actions"].append("new_action")
        c["plugin_api_version"] = "1.9"
        c["ui"]["widgets_contract_version"] = int(c["ui"]["widgets_contract_version"]) + 1
        c["capabilities"]["registry"][0]["deprecated_in"] = "0.200.0"
        c["hooks"][0]["summary"] = "说明文字可改"

    assert _mutate(frozen, additive) == []


@pytest.mark.parametrize(
    ("label", "mutation"),
    [
        ("删钩子", lambda c: c["hooks"].remove(_hook(c, "on_ui_action"))),
        ("删参数", lambda c: _hook(c, "on_event")["params"].pop()),
        ("新增必填参数", lambda c: _hook(c, "on_event")["params"].append({"name": "x", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False})),
        ("可选变必填", lambda c: c["context"]["members"][1]["params"][-1].update(has_default=False)),
        ("删 ctx 字段", lambda c: c["context"]["fields"].remove("storage")),
        ("删 facade 方法", lambda c: c["facades"]["ctx.messages[live]"].pop(0)),
        ("删能力", lambda c: c["capabilities"]["registry"].pop(0)),
        ("能力改 since", lambda c: c["capabilities"]["registry"][0].update(since="9.9.9")),
        ("能力标记移除", lambda c: c["capabilities"]["registry"][0].update(removed_in="0.200.0")),
        ("facade 不再提供能力", lambda c: c["capabilities"]["ctx_capabilities_has"]["entry"].pop(0)),
        ("删 manifest 字段", lambda c: c["manifest"]["fields"].pop("payment_sources")),
        ("收窄 manifest 类型", lambda c: c["manifest"]["fields"].__setitem__("plugin_api", ["string"])),
        ("删控件", lambda c: c["ui"]["widgets"].pop(0)),
        ("删 Bridge 方法", lambda c: c["bridge"]["methods"].pop("config.get")),
        ("改 Bridge 协议", lambda c: c["bridge"].update(protocol=2)),
        ("删动作类型", lambda c: c["actions"].remove("send_message")),
        ("删事件类型", lambda c: c["events"]["types"].remove("message")),
        ("删权限", lambda c: c["permissions"].remove("send_message")),
        ("删 host.read 资源", lambda c: c["host_read_resources"].pop(0)),
        ("Plugin API 升 MAJOR", lambda c: c.update(plugin_api_version="2.0")),
        ("控件契约回退", lambda c: c["ui"].update(widgets_contract_version=1)),
    ],
)
def test_detector_flags_breaking_changes(frozen: dict, label: str, mutation) -> None:
    assert _mutate(frozen, mutation), label


# ── manifest Schema ────────────────────────────────────


@pytest.mark.parametrize("path", EXAMPLES + BUILTINS, ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}")
def test_shipped_manifests_validate(path: Path, contract_schema: Draft202012Validator) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    install = Draft202012Validator(json.loads(contract.MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8")))
    assert [e.message for e in install.iter_errors(data)] == []
    assert [f"{list(e.absolute_path)}: {e.message}" for e in contract_schema.iter_errors(data)] == []


def test_examples_cover_contract_surfaces() -> None:
    """示例插件覆盖契约的主要扩展面，golden 测试依赖它们。"""

    seen: set[str] = set()
    for path in EXAMPLES:
        data = json.loads(path.read_text(encoding="utf-8"))
        seen.update(key for key in ("event_subscriptions", "interaction_entries", "middleware", "ui", "config_schema") if data.get(key))
    assert seen >= {"event_subscriptions", "interaction_entries", "middleware", "ui", "config_schema"}


_BASE = {"name": "demo", "version": "1.0.0"}


@pytest.mark.parametrize(
    ("label", "patch"),
    [
        ("缺 name/key", {"name": None}),
        ("middleware stage", {"middleware": [{"stage": "sideways", "handler": "on_x"}]}),
        ("middleware 超时上限", {"middleware": [{"stage": "outbound", "timeout_ms": 99999}]}),
        ("entry_policy 会话范围", {"interaction_entries": [{"key": "start", "entry_policy": {"session_scope": "galaxy"}}]}),
        ("entry_policy 参与方式", {"interaction_entries": [{"key": "start", "entry_policy": {"participant_policy": "everyone"}}]}),
        ("legacy_entry_keys 缺 key", {"interaction_entries": [{"key": "start", "legacy_entry_keys": [{"session_scope": "chat"}]}]}),
        ("入口缺 key", {"interaction_entries": [{"title": "x"}]}),
        ("事件类型", {"event_subscriptions": [{"events": ["telepathy"]}]}),
        ("卡片 slot", {"ui": {"cards": [{"id": "c", "slot": "nowhere", "source": {"action": "c"}}]}}),
        ("页面 Bridge 权限", {"ui": {"pages": [{"id": "p", "file": "ui/p.html", "bridge": ["root"]}]}}),
        ("页面文件", {"ui": {"pages": [{"id": "p", "file": "../evil.js"}]}}),
        ("reads 写法", {"reads": ["account admins"]}),
        ("payment_sources 缺 id", {"payment_sources": [{"title": "x"}]}),
    ],
)
def test_contract_schema_rejects_common_mistakes(label: str, patch: dict, contract_schema: Draft202012Validator) -> None:
    data = {**_BASE, **patch}
    data = {k: v for k, v in data.items() if v is not None}
    assert list(contract_schema.iter_errors(data)), label


def test_contract_schema_allows_unknown_fields(contract_schema: Draft202012Validator) -> None:
    data = {
        **_BASE,
        "future_field": {"anything": True},
        "interaction_entries": [{"key": "start", "future": 1, "entry_policy": {"session_scope": "chat", "future": 2}}],
        "ui": {"cards": [{"id": "c", "slot": "dashboard", "source": {"action": "c"}, "layout": {"type": "stats"}, "x": 1}]},
    }
    assert list(contract_schema.iter_errors(data)) == []


# ── ctx.capabilities 快照与钩子签名 ───────────────────


def test_ctx_capabilities_snapshot_superset_of_baseline(current: dict, frozen: dict) -> None:
    for facade, names in frozen["capabilities"]["ctx_capabilities_has"].items():
        missing = sorted(set(names) - set(current["capabilities"]["ctx_capabilities_has"][facade]))
        assert missing == [], f"{facade} 不再提供 {missing}"
    for facade, attrs in frozen["capabilities"]["legacy_attrs_true"].items():
        missing = sorted(set(attrs) - set(current["capabilities"]["legacy_attrs_true"][facade]))
        assert missing == [], f"{facade} 旧能力属性丢失 {missing}"


def test_plugin_base_hooks_callable_with_baseline_arguments(frozen: dict) -> None:
    for hook in frozen["hooks"]:
        if hook["kind"] != "plugin_method":
            continue
        func = getattr(Plugin, hook["name"])
        assert inspect.iscoroutinefunction(func), hook["name"]
        positional = [p["name"] for p in hook["params"] if p["kind"] == "POSITIONAL_OR_KEYWORD" and not p["has_default"]]
        inspect.signature(func).bind(*[object()] * len(positional))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_hook_overrides_match_contract(path: Path, frozen: dict) -> None:
    import importlib

    module = importlib.import_module(f"examples.plugins.{path.parent.name}")
    cls = module.PLUGIN_CLASS
    for hook in frozen["hooks"]:
        if hook["kind"] != "plugin_method":
            continue
        method = cls.__dict__.get(hook["name"])
        if method is None:
            continue
        assert inspect.iscoroutinefunction(method), f"{cls.__name__}.{hook['name']} 必须是 async"
        positional = [p for p in hook["params"] if p["kind"] == "POSITIONAL_OR_KEYWORD" and not p["has_default"]]
        inspect.signature(method).bind(*[object()] * len(positional))
