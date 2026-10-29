"""插件平台 P3 第二部分（W1 迁移）：通用规则页声明与规则试运行协议。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import plugin_compat
from app.db.models.plugin import InstalledPlugin
from app.services import rule_dry_run_service
from app.settings import settings
from app.worker.plugins import capabilities as caps_mod
from app.worker.plugins import plugin_ui
from app.worker.plugins.loader import _clear_installed_module_cache


class _DB:
    def __init__(self, row: InstalledPlugin | None) -> None:
        self.row = row

    async def get(self, model, key):  # noqa: ANN001
        if model is InstalledPlugin and self.row is not None and key == self.row.key:
            return self.row
        return None


def _installed(key: str) -> InstalledPlugin:
    return InstalledPlugin(
        key=key,
        source="repo",
        source_url="https://github.com/Anoyou/telebot-plugins",
        enabled=True,
        trust_tier="community",
        signature_ok=True,
    )


def _write_plugin(root, key: str, body: str) -> None:
    plugin_dir = root / key
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": key, "version": "1.0.0"}), encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(
        "from .manifest import MANIFEST\nfrom .plugin import *  # noqa\nfrom .plugin import DemoPlugin\nPLUGIN_CLASS = DemoPlugin\n",
        encoding="utf-8",
    )
    (plugin_dir / "manifest.py").write_text(
        "from app.worker.plugins.manifest import Manifest\n"
        f"MANIFEST = Manifest(key={key!r}, display_name={key!r}, version='1.0.0')\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "import asyncio\nfrom app.worker.plugins.base import Plugin\n\n"
        f"class DemoPlugin(Plugin):\n    key = {key!r}\n\n" + body,
        encoding="utf-8",
    )


@pytest.fixture
def plugin_root(monkeypatch, tmp_path):
    root = tmp_path / "installed"
    monkeypatch.setattr(settings, "plugins_installed_dir", str(root))
    keys: list[str] = []
    yield root, keys
    for key in keys:
        _clear_installed_module_cache(key)


async def _run(root, keys, key: str, body: str, cfg: dict, **sample):
    _write_plugin(root, key, body)
    keys.append(key)
    return await rule_dry_run_service.plugin_dry_run(
        _DB(_installed(key)),
        key,
        cfg,
        sample_message=sample.get("message", "hi"),
        sample_chat_type=sample.get("chat_type"),
        sample_chat_id=sample.get("chat_id"),
    )


@pytest.mark.asyncio
async def test_new_protocol_dry_run_rule_receives_sample_and_returns_logs(plugin_root) -> None:
    root, keys = plugin_root
    body = (
        "def dry_run_rule(config, sample):\n"
        "    hit = sample['message'] == config.get('kw')\n"
        "    return {'matched': hit, 'output': f\"{sample['chat_type']}:{sample['chat_id']}\",\n"
        "            'logs': [{'step': 'match', 'msg': 'kw=' + config.get('kw', '')}, '纯文本日志', {'msg': ''}]}\n"
        "def _dry_run_match(*args):\n"
        "    raise AssertionError('有新协议时不应调用旧协议')\n"
    )
    result = await _run(root, keys, "w1_new", body, {"kw": "hi"}, chat_type="group", chat_id=-100)
    assert result["protocol"] == rule_dry_run_service.PROTOCOL_NEW
    assert result["matched"] is True
    assert result["output"] == "group:-100"
    assert result["logs"] == [{"step": "match", "msg": "kw=hi"}, {"step": "plugin", "msg": "纯文本日志"}]


@pytest.mark.asyncio
async def test_new_protocol_async_tuple_timeout_and_error(plugin_root, monkeypatch) -> None:
    root, keys = plugin_root
    ok = await _run(
        root, keys, "w1_async",
        "async def dry_run_rule(config, sample):\n    return True, 'async-ok'\n",
        {},
    )
    assert (ok["matched"], ok["output"], ok["logs"]) == (True, "async-ok", [])

    monkeypatch.setattr(rule_dry_run_service, "DRY_RUN_TIMEOUT_SECONDS", 0.05)
    slow = await _run(
        root, keys, "w1_slow",
        "async def dry_run_rule(config, sample):\n    await asyncio.sleep(1)\n    return True, 'late'\n",
        {},
    )
    assert slow["matched"] is False and "超时" in slow["output"]

    boom = await _run(
        root, keys, "w1_boom",
        "def dry_run_rule(config, sample):\n    raise ValueError('坏规则')\n",
        {},
    )
    assert boom["matched"] is False and "坏规则" in boom["output"]


@pytest.mark.asyncio
async def test_legacy_protocol_args_and_logs_come_from_plugin_compat(plugin_root) -> None:
    root, keys = plugin_root
    body = "def _dry_run_match(*args):\n    return True, repr(args[1:])\n"
    # auto_reply 旧版第三个参数是会话类型
    auto = await _run(root, keys, plugin_compat.AUTO_REPLY, body, {"scope": "private"}, chat_type="private", chat_id=7)
    assert auto["protocol"] == rule_dry_run_service.PROTOCOL_LEGACY
    assert auto["output"] == "('hi', 'private', 7)"
    assert any(item["step"] == "scope" for item in auto["logs"])
    # 其它旧版插件：(config, message, chat_id)
    other = await _run(root, keys, plugin_compat.AUTOREPEAT, body, {"target_chat_id": 1}, chat_id=9)
    assert other["output"] == "('hi', 9)"
    assert other["logs"][0]["step"] == "target"
    assert other["logs"][-1] == {"step": "result", "msg": "命中"}


@pytest.mark.asyncio
async def test_plugin_without_dry_run_and_uninstalled(plugin_root) -> None:
    root, keys = plugin_root
    none = await _run(root, keys, "w1_none", "X = 1\n", {})
    assert none["protocol"] == rule_dry_run_service.PROTOCOL_NONE
    assert "dry_run_rule" in none["output"]
    missing = await rule_dry_run_service.plugin_dry_run(_DB(None), "nope", {}, sample_message="x")
    assert missing["matched"] is False and "未安装" in missing["output"]


@pytest.mark.asyncio
async def test_agent_dry_run_uses_generic_protocol(plugin_root) -> None:
    root, keys = plugin_root
    _write_plugin(root, "w1_agent", "def dry_run_rule(config, sample):\n    return {'matched': True, 'output': 'ok', 'logs': ['a']}\n")
    keys.append("w1_agent")
    rule = SimpleNamespace(feature_key="w1_agent", config={"x": 1}, id=5)
    result = await rule_dry_run_service.dry_run_rule(_DB(_installed("w1_agent")), rule, sample_message="hi")
    assert result["matched"] is True
    assert result["detail"]["logs"] == [{"step": "plugin", "msg": "a"}]


def test_rules_capabilities_registered() -> None:
    names = {item.name: item for item in caps_mod.CAPABILITIES}
    assert names["ui.rules_page"].since == caps_mod._W1_RELEASE
    assert names["rules.dry_run"].since == caps_mod._W1_RELEASE


def test_contract_declares_rules_keys_and_dry_run_hook() -> None:
    contract = plugin_ui.load_contract()
    assert contract["contract_version"] >= 2
    keys = {item["key"] for item in contract["keys"]}
    assert {"x-rule-schema", "x-rule-summary", "x-dry-run", "x-ui-visible-if"} <= keys
    assert contract["hooks"]["dry_run_rule"]["timeout_ms"] == 3000


def test_rule_schema_of_and_lint() -> None:
    rule_schema = {"type": "object", "properties": {"scope": {"type": "string"}, "group_ids": {"type": "array"}}}
    good = {
        "x-ui-mode": "rules",
        "properties": {},
        "x-rule-schema": rule_schema,
        "x-rule-summary": ["scope"],
        "x-dry-run": {"sample_fields": ["message", "chat_type", "chat_id"]},
    }
    rule_schema["properties"]["group_ids"]["x-ui-visible-if"] = {"field": "scope", "equals": "group_specific"}
    assert plugin_ui.rule_schema_of(good) is rule_schema
    assert plugin_ui.lint_rules_declaration(good) == []
    legacy = {"x-ui-mode": "rules", "properties": {"target_chat_id": {"type": "integer"}}}
    assert plugin_ui.rule_schema_of(legacy) is legacy
    assert plugin_ui.rule_schema_of({"x-ui-mode": "single", "properties": {}}) is None

    bad = {
        "x-ui-mode": "single",
        "properties": {"a": {"type": "string", "x-ui-visible-if": {"field": "missing"}}},
        "x-rule-schema": {"properties": {"b": {"type": "string", "x-ui-widget": "nope"}}},
        "x-rule-summary": ["zzz"],
        "x-dry-run": {"sample_fields": ["sender"]},
    }
    warnings = plugin_ui.lint_rules_declaration(bad)
    joined = "\n".join(warnings)
    assert "只在 x-ui-mode=rules 时生效" in joined
    assert "x-rule-schema.b: nope" in joined
    assert "x-rule-summary" in joined
    assert "sample_fields" in joined
    assert "不存在的字段 missing" in joined
