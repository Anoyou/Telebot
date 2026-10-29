"""插件 golden 测试：典型插件在宿主上的输出快照（插件稳定契约 1.0）。

覆盖 examples/plugins 里的典型插件，逐项记录“宿主怎么理解插件 / 插件在宿主上产出什么”：

- manifest：安装解析（_read_plugin_metadata）、交互入口整理、声明式 UI（卡片 / 页面 / 菜单）整理；
- 钩子：Event Bus（event_bus_demo 全部夹具、hello_ping、webhook_receiver、ui_page_demo）、交互入口
  （with_interaction，纯返回与缓冲 MessageOps 两种 facade）、卡片 / 菜单钩子（on_ui_card / on_ui_action）；
- 中间件：word_guard_mw / signature_mw 的 outbound / inbound / stream_preview / delivered。

快照：app/tests/fixtures/plugin_golden/golden.json。宿主行为有意改变时，用
``TELEPILOT_UPDATE_GOLDEN=1 pytest app/tests/test_plugin_golden.py`` 重新生成，并在 PR 里说明为什么变。
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_ROOT = REPO_ROOT / "examples" / "plugins"
GOLDEN_PATH = Path(__file__).with_name("fixtures") / "plugin_golden" / "golden.json"
UPDATE = os.environ.get("TELEPILOT_UPDATE_GOLDEN") == "1"


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Path):
        try:
            return str(value.relative_to(REPO_ROOT))
        except ValueError:
            return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _plugin(name: str) -> Any:
    module = importlib.import_module(f"examples.plugins.{name}")
    return module, module.PLUGIN_CLASS()


def _metadata(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES_ROOT / name / "plugin.json").read_text(encoding="utf-8"))


class _Storage:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data: dict[str, Any] = dict(data or {})

    async def get(self, key: Any, default: Any = None) -> Any:
        return self.data.get(key, default)

    async def set(self, key: Any, value: Any, **_kw: Any) -> bool:
        self.data[key] = value
        return True

    async def incr(self, key: Any, amount: int = 1, **_kw: Any) -> int:
        self.data[key] = int(self.data.get(key, 0) or 0) + amount
        return self.data[key]


# ── 各段快照 ──────────────────────────────────────────


def _manifest_section() -> dict[str, Any]:
    from app.services import account_bot_service
    from app.services.remote_plugin_service import _read_plugin_metadata, lint_plugin_metadata_files
    from app.worker.plugins import plugin_ui

    out: dict[str, Any] = {}
    for plugin_dir in sorted(p for p in EXAMPLES_ROOT.iterdir() if (p / "plugin.json").is_file()):
        meta = _read_plugin_metadata(plugin_dir, fallback_name=plugin_dir.name, require_platform_capabilities=True)
        raw = _metadata(plugin_dir.name)
        record: dict[str, Any] = {
            "metadata": _jsonable(meta),
            "lint": lint_plugin_metadata_files(plugin_dir),
            "interaction_entries": [
                account_bot_service.normalize_interaction_entry_manifest(item) for item in raw.get("interaction_entries") or []
            ],
        }
        if raw.get("ui"):
            cards, card_problems = plugin_ui.normalize_ui_cards(raw["ui"], reads=raw.get("reads"))
            pages, menu_actions, ui_problems = plugin_ui.normalize_ui_entries(raw["ui"])
            record["ui"] = {"cards": cards, "pages": pages, "actions": menu_actions, "problems": card_problems + ui_problems}
        out[plugin_dir.name] = _jsonable(record)
    return out


async def _event_section() -> dict[str, Any]:
    out: dict[str, Any] = {}
    _, demo = _plugin("event_bus_demo")
    fixtures = EXAMPLES_ROOT / "event_bus_demo" / "fixtures"
    for path in sorted(fixtures.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        out[f"event_bus_demo/{path.name}"] = await demo.on_event(SimpleNamespace(messages=None, log=None), payload)

    _, ping = _plugin("hello_ping")
    base = {
        "source": {"type": "message", "channel": "interaction_bot", "account_id": 1, "chat_id": -100123, "message_id": 456},
        "message": {"chat_id": -100123, "message_id": 456, "text": "ping"},
        "chat": {"id": -100123, "type": "supergroup"},
        "sender": {"user_id": 789, "display_name": "Alice"},
    }
    out["hello_ping/ping"] = await ping.on_event(SimpleNamespace(messages=None, log=None), base)
    out["hello_ping/miss"] = await ping.on_event(
        SimpleNamespace(messages=None, log=None), {**base, "message": {**base["message"], "text": "hello"}}
    )

    _, hook = _plugin("webhook_receiver")
    payload = json.loads((EXAMPLES_ROOT / "webhook_receiver" / "fixtures" / "orders_paid.json").read_text(encoding="utf-8"))
    ctx = SimpleNamespace(config={"target_chat_id": -100123, "title": "订单事件"}, log=None)
    out["webhook_receiver/orders_paid"] = await hook.on_event(ctx, payload)

    _, page_demo = _plugin("ui_page_demo")
    storage = _Storage()
    ui_ctx = SimpleNamespace(storage=storage, messages=None, log=None)
    out["ui_page_demo/on_event(ping)"] = await page_demo.on_event(ui_ctx, base)
    out["ui_page_demo/storage_after_ping"] = dict(storage.data)
    return _jsonable(out)


async def _interaction_section() -> dict[str, Any]:
    from app.worker.plugins.message_ops import BufferedMessageOps

    _, plugin = _plugin("with_interaction")
    payload = {
        "entry_key": "start_with_interaction",
        "module_config": {"response_text": "欢迎"},
        "event": {"type": "message"},
        "source": {"type": "message", "channel": "interaction_bot", "account_id": 1, "chat_id": -100777, "message_id": 9},
        "message": {"chat_id": -100777, "message_id": 9, "text": "开始"},
        "chat": {"id": -100777, "type": "supergroup"},
        "sender": {"user_id": 42, "display_name": "Bob"},
        "actor": {"user_id": 42, "display_name": "Bob"},
    }
    out: dict[str, Any] = {}
    out["with_interaction/returned"] = await plugin.on_interaction(SimpleNamespace(messages=None, log=None), "start_with_interaction", payload)
    ops = BufferedMessageOps()
    returned = await plugin.on_interaction(SimpleNamespace(messages=ops, log=None), "start_with_interaction", payload)
    out["with_interaction/buffered"] = {"returned": returned, "buffered_actions": ops.actions}
    out["with_interaction/other_entry"] = await plugin.on_interaction(SimpleNamespace(messages=None, log=None), "nope", payload)
    return _jsonable(out)


async def _ui_hooks_section() -> dict[str, Any]:
    _, plugin = _plugin("ui_page_demo")
    ctx = SimpleNamespace(storage=_Storage({"pings": 3}), log=None)
    no_storage = SimpleNamespace(storage=None, log=None)
    return _jsonable(
        {
            "on_ui_card(ping_stats)": await plugin.on_ui_card(ctx, "ping_stats", {"plugin_key": "ui_page_demo"}),
            "on_ui_card(ping_stats, 旧宿主无 storage)": await plugin.on_ui_card(no_storage, "ping_stats", {}),
            "on_ui_card(unknown)": await plugin.on_ui_card(ctx, "unknown", {}),
            "on_ui_action(summarize, 菜单)": await plugin.on_ui_action(ctx, "summarize", {"plugin_key": "ui_page_demo"}),
            "on_ui_action(summarize, 页面)": await plugin.on_ui_action(ctx, "summarize", {"page_id": "panel"}),
            "on_ui_action(unknown)": await plugin.on_ui_action(ctx, "nope", {}),
        }
    )


async def _middleware_section() -> dict[str, Any]:
    from app.worker.plugins import middleware
    from app.worker.plugins.base import PluginContext

    out: dict[str, Any] = {}
    for name in ("word_guard_mw", "signature_mw"):
        module, plugin = _plugin(name)
        metadata = _metadata(name)
        manifest = module.MANIFEST
        manifest.middleware = list(metadata.get("middleware") or [])
        type(plugin)._manifest = manifest
        storage = _Storage()
        config = {"compact_tables": True} if name == "signature_mw" else {}
        state = SimpleNamespace(
            account_id=1,
            generation=1,
            instances={manifest.key: plugin},
            contexts={manifest.key: PluginContext(account_id=1, feature_key=manifest.key, config=config, storage=storage)},
            plugin_circuits={},
            redis=None,
        )
        action = {"type": "send_message", "chat_id": -1, "text": "含违禁词的回复", "context": {"plugin_key": "other"}}
        rich = {
            "type": "send_rich_message",
            "chat_id": -1,
            "rich_message": {"markdown": "| a |\n|---|\n| 1 |"},
            "context": {"plugin_key": "other"},
        }
        outbound = await middleware.run_outbound(state, [action, rich], source_plugin_key="other")
        preview = await middleware.run_stream_preview(state, "流式违禁词", source_plugin_key="other")
        inbound = await middleware.run_inbound(
            state, {"event_type": "message", "message": {"text": "违禁词"}}, event_type="message", source="userbot"
        )
        delivered = await middleware.run_delivered(state, [middleware.delivered_summary(outbound[0], ok=True)])
        out[name] = {
            "outbound": outbound,
            "stream_preview": preview,
            "inbound": {
                "dropped": getattr(inbound, "dropped", None) is not None,
                "value": getattr(inbound, "value", None),
            },
            "delivered_calls": delivered,
            "storage": dict(storage.data),
        }
    return _jsonable(out)


def _build() -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        return {
            "events": await _event_section(),
            "interaction": await _interaction_section(),
            "ui_hooks": await _ui_hooks_section(),
            "middleware": await _middleware_section(),
        }

    data = {"manifest": _manifest_section()}
    data.update(asyncio.run(run()))
    return data


@pytest.fixture(scope="module")
def actual() -> dict[str, Any]:
    return _build()


@pytest.fixture(scope="module")
def golden(actual: dict[str, Any]) -> dict[str, Any]:
    if UPDATE or not GOLDEN_PATH.is_file():
        if not UPDATE:
            pytest.fail(f"{GOLDEN_PATH} 不存在：用 TELEPILOT_UPDATE_GOLDEN=1 生成")
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("section", ["manifest", "events", "interaction", "ui_hooks", "middleware"])
def test_plugin_golden(section: str, actual: dict[str, Any], golden: dict[str, Any]) -> None:
    want = golden.get(section)
    got = json.loads(json.dumps(actual.get(section), ensure_ascii=False, sort_keys=True))
    assert want is not None, f"golden 缺少 {section}"
    for key in sorted(set(want) | set(got)):
        assert got.get(key) == want.get(key), (
            f"{section}/{key} 与 golden 不一致；若是有意的宿主行为变化，用 TELEPILOT_UPDATE_GOLDEN=1 重新生成并在 PR 说明"
        )


def test_golden_covers_every_example(golden: dict[str, Any]) -> None:
    examples = {p.name for p in EXAMPLES_ROOT.iterdir() if (p / "plugin.json").is_file()}
    assert set(golden["manifest"]) == examples
