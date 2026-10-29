"""插件平台化 P2（0.109.0）：消息管线中间件。

覆盖：声明解析、outbound 字段白名单回滚 / fail-open / 超时计入熔断 / 拒绝、链长上限、
结构共享（不深拷贝大 rich_message）、render_profile 与 core.*、inbound 补丁与丢弃、
delivered 异步回调（worker 与交互 Bot IPC 两条路）、流式中间态、访客合并、阻塞检测、
stream_complete extra_body、能力 API。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import account_bot_runtime, account_bot_service, render_profile
from app.services.interaction.action_core import ActionHandlers, run_action_batch
from app.services.interaction.delivery import InteractionDeliveryExecutor
from app.services.interaction.guest_reply import GuestReplyBuffer
from app.worker.plugins import blocking_monitor, middleware, plugin_stats
from app.worker.plugins import loader as loader_mod
from app.worker.plugins.base import Plugin, PluginContext
from app.worker.plugins.manifest import Manifest


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    plugin_stats.get_recorder().reset_local()
    plugin_stats.get_recorder().set_redis_factory(None)
    yield
    plugin_stats.get_recorder().reset_local()


def _plugin(key: str, decl: list[dict[str, Any]], methods: dict[str, Any], *, permissions: list[str] | None = None, render: dict[str, Any] | None = None) -> Plugin:
    manifest = Manifest(
        key=key,
        display_name=key,
        permissions=list(permissions if permissions is not None else [middleware.MIDDLEWARE_PERMISSION]),
        middleware=decl,
        render_profile=render,
    )
    cls = type(f"P_{key}", (Plugin,), {"key": key, "display_name": key, "_manifest": manifest, **methods})
    return cls()


def _state(*plugins: Plugin, account_id: int = 7) -> Any:
    state = loader_mod._AccountState(account_id)
    for inst in plugins:
        key = type(inst)._manifest.key
        state.instances[key] = inst
        state.contexts[key] = PluginContext(account_id=account_id, feature_key=key)
    return state


def _stat(plugin_key: str, hook: str) -> dict[str, Any]:
    rows = [row for row in plugin_stats.get_recorder().local_snapshot(plugin_key)["hooks"] if row["hook"] == hook]
    out: dict[str, Any] = {}
    for row in rows:
        for name, value in row.items():
            if isinstance(value, int):
                out[name] = out.get(name, 0) + value
    return out


def _signature_plugin(key: str = "sig", *, sources: list[str] | None = None, order: int = 500) -> Plugin:
    async def on_outbound(self, ctx, action, mw):
        if isinstance(action.get("text"), str):
            action["text"] = action["text"] + " —sig"
            return action
        return None

    return _plugin(
        key,
        [{"stage": "outbound", "handler": "on_outbound", "order": order, "match": {"sources": sources or ["any_plugin"]}}],
        {"on_outbound": on_outbound},
    )


# ─────────────────────────────── 声明 ───────────────────────────────
def test_parse_middleware_defaults_and_clamps() -> None:
    specs, problems = middleware.parse_middleware(
        [
            {"stage": "outbound", "handler": "on_outbound"},
            {"stage": "outbound", "handler": "late", "order": 950, "timeout_ms": 10},
            {"stage": "delivered", "handler": "on_delivered", "match": {"action_types": ["send_message"]}},
            {"stage": "bogus", "handler": "x"},
            {"stage": "outbound", "handler": "_private"},
        ],
        plugin_key="demo",
        permissions=[],
    )
    assert [spec.handler for spec in specs] == ["on_outbound", "late", "on_delivered"]
    assert specs[0].order == 500 and specs[0].timeout_ms == 250 and specs[0].sources == {"self"}
    assert specs[1].order == middleware.ORDER_MAX and specs[1].timeout_ms == middleware.MIN_TIMEOUT_MS
    assert specs[2].action_types == {"send_message"}
    assert len(problems) == 4


def test_parse_middleware_any_plugin_and_inbound_need_permission() -> None:
    decl = [
        {"stage": "outbound", "handler": "on_outbound", "match": {"sources": ["any_plugin"]}},
        {"stage": "inbound", "handler": "on_inbound"},
    ]
    specs, problems = middleware.parse_middleware(decl, plugin_key="demo", permissions=[])
    assert [spec.stage for spec in specs] == ["outbound"]
    assert specs[0].sources == {"self"}
    assert any("message_middleware" in item for item in problems)
    with pytest.raises(middleware.MiddlewareDeclarationError):
        middleware.parse_middleware(decl, plugin_key="demo", permissions=[], strict=True)
    specs, problems = middleware.parse_middleware(decl, plugin_key="demo", permissions=["message_middleware"], strict=True)
    assert problems == [] and specs[0].sources == {"any_plugin"} and specs[1].events == {"message"}


def test_parse_middleware_rejects_critical_and_unknown_types() -> None:
    with pytest.raises(middleware.MiddlewareDeclarationError) as exc:
        middleware.parse_middleware(
            [{"stage": "outbound", "handler": "h", "critical": True, "match": {"action_types": ["payout"]}}],
            plugin_key="demo",
            strict=True,
        )
    assert "critical" in str(exc.value) and "payout" in str(exc.value)


def test_remote_plugin_schema_validates_middleware_and_render_profile() -> None:
    from app.services.remote_plugin_service import PluginMetadataSchema

    ok = PluginMetadataSchema(
        name="demo",
        permissions=["message_middleware"],
        middleware=[{"stage": "inbound", "handler": "on_inbound"}],
        render_profile={"table": {"compact": True}},
    )
    assert ok.render_profile == {"table": {"compact": True}}
    with pytest.raises(ValueError):
        PluginMetadataSchema(name="demo", middleware=[{"stage": "inbound", "handler": "on_inbound"}])
    with pytest.raises(ValueError):
        PluginMetadataSchema(name="demo", render_profile={"table": {"compact": "yes"}})


# ─────────────────────────────── outbound ───────────────────────────────
@pytest.mark.asyncio
async def test_outbound_signature_applies_to_other_plugins_and_shares_structure() -> None:
    state = _state(_signature_plugin())
    rich = {"blocks": [{"type": "table", "cells": [[{"text": str(i)}] for i in range(500)]}]}
    actions = [
        {"type": "send_message", "chat_id": -1, "text": "hi", "context": {"plugin_key": "other"}},
        {"type": "send_rich_message", "chat_id": -1, "rich_message": rich, "text": "cap", "context": {"plugin_key": "other"}},
        {"type": "payout", "chat_id": -1, "amount": 10, "text": "转账"},
    ]
    original_text = actions[0]["text"]
    out = await middleware.run_outbound(state, actions, source_plugin_key="other")
    assert out[0]["text"] == "hi —sig" and actions[0]["text"] == original_text
    # 结构共享：rich_message 没被复制
    assert out[1]["rich_message"] is rich
    assert out[1]["text"] == "cap —sig"
    # 资金类动作从不进插件中间件
    assert out[2] is actions[2]
    assert _stat("sig", "middleware.outbound")["ok"] == 2


@pytest.mark.asyncio
async def test_outbound_self_source_ignores_other_plugins() -> None:
    state = _state(_signature_plugin(sources=["self"]))
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "x"}], source_plugin_key="other")
    assert out[0]["text"] == "x"
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "x"}], source_plugin_key="sig")
    assert out[0]["text"] == "x —sig"


@pytest.mark.asyncio
async def test_outbound_field_violation_rolls_back_and_counts_failure() -> None:
    async def evil(self, ctx, action, mw):
        action["chat_id"] = 999
        action["text"] = "hijacked"
        return action

    state = _state(_plugin("evil", [{"stage": "outbound", "handler": "evil", "match": {"sources": ["any_plugin"]}}], {"evil": evil}))
    action = {"type": "send_message", "chat_id": -1, "text": "ok", "settlement": {"amount": 5}}
    out = await middleware.run_outbound(state, [action], source_plugin_key="game")
    assert out[0] == action
    stats = _stat("evil", "middleware.outbound")
    assert stats["fail"] == 1
    assert state.plugin_circuits["evil"]["failures"] == 1


@pytest.mark.asyncio
async def test_outbound_settlement_change_is_rolled_back() -> None:
    async def tweak(self, ctx, action, mw):
        action["settlement"] = {"amount": 500}
        return action

    state = _state(_plugin("t", [{"stage": "outbound", "handler": "tweak", "match": {"sources": ["any_plugin"]}}], {"tweak": tweak}))
    action = {"type": "send_message", "chat_id": -1, "text": "赢了", "settlement": {"amount": 5}}
    out = await middleware.run_outbound(state, [action], source_plugin_key="game")
    assert out[0]["settlement"] == {"amount": 5}


@pytest.mark.asyncio
async def test_outbound_nested_mutation_fails_open() -> None:
    async def mutate(self, ctx, action, mw):
        action["reply_markup"]["inline_keyboard"].append([{"text": "x"}])  # 只读视图
        return action

    state = _state(_plugin("m", [{"stage": "outbound", "handler": "mutate", "match": {"sources": ["any_plugin"]}}], {"mutate": mutate}))
    markup = {"inline_keyboard": [[{"text": "a", "callback_data": "a"}]]}
    action = {"type": "send_message", "chat_id": -1, "text": "t", "reply_markup": markup}
    out = await middleware.run_outbound(state, [action], source_plugin_key="m")
    assert out[0]["reply_markup"] is markup and len(markup["inline_keyboard"]) == 1
    assert _stat("m", "middleware.outbound")["fail"] == 1


@pytest.mark.asyncio
async def test_outbound_thaw_and_replace_nested_value() -> None:
    async def add_button(self, ctx, action, mw):
        markup = mw.thaw(action.get("reply_markup") or {"inline_keyboard": []})
        markup["inline_keyboard"].append([{"text": "更多", "url": "https://example.com"}])
        action["reply_markup"] = markup
        return action

    state = _state(_plugin("b", [{"stage": "outbound", "handler": "add_button"}], {"add_button": add_button}))
    markup = {"inline_keyboard": [[{"text": "a", "callback_data": "a"}]]}
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "t", "reply_markup": markup}], source_plugin_key="b")
    assert len(out[0]["reply_markup"]["inline_keyboard"]) == 2
    assert len(markup["inline_keyboard"]) == 1


@pytest.mark.asyncio
async def test_outbound_exception_and_malformed_fail_open() -> None:
    async def boom(self, ctx, action, mw):
        raise RuntimeError("boom")

    async def weird(self, ctx, action, mw):
        return ["not", "a", "dict"]

    state = _state(
        _plugin("a", [{"stage": "outbound", "handler": "boom", "order": 1, "match": {"sources": ["any_plugin"]}}], {"boom": boom}),
        _plugin("b", [{"stage": "outbound", "handler": "weird", "order": 2, "match": {"sources": ["any_plugin"]}}], {"weird": weird}),
        _signature_plugin("c"),
    )
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "x"}], source_plugin_key="z")
    assert out[0]["text"] == "x —sig"
    assert _stat("a", "middleware.outbound")["fail"] == 1
    assert _stat("b", "middleware.outbound")["fail"] == 1


@pytest.mark.asyncio
async def test_outbound_timeout_counts_toward_circuit(monkeypatch) -> None:
    async def slow(self, ctx, action, mw):
        await asyncio.sleep(1)
        action["text"] = "late"
        return action

    monkeypatch.setattr(loader_mod.app_settings, "plugin_circuit_failure_threshold", 2, raising=False)
    state = _state(_plugin("slow", [{"stage": "outbound", "handler": "slow", "timeout_ms": 50}], {"slow": slow}))
    for _ in range(3):
        out = await middleware.run_outbound(state, [{"type": "send_message", "text": "x"}], source_plugin_key="slow")
        assert out[0]["text"] == "x"
    stats = _stat("slow", "middleware.outbound")
    assert stats["timeout"] == 2 and stats["circuit_open"] == 1
    assert plugin_stats.get_recorder().local_snapshot("slow")["hooks"][0]["last_error"]


@pytest.mark.asyncio
async def test_outbound_sync_blocking_beyond_timeout_is_discarded() -> None:
    async def blocking(self, ctx, action, mw):
        time.sleep(0.25)
        action["text"] = "blocked"
        return action

    state = _state(_plugin("blk", [{"stage": "outbound", "handler": "blocking", "timeout_ms": 50}], {"blocking": blocking}))
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "x"}], source_plugin_key="blk")
    assert out[0]["text"] == "x"
    stats = _stat("blk", "middleware.outbound")
    assert stats["timeout"] == 1 and stats["blocked"] == 1 and stats["blocking_ms"] >= 200


@pytest.mark.asyncio
async def test_outbound_reject_marks_action_and_batch_reports_failure() -> None:
    async def guard(self, ctx, action, mw):
        if "违禁" in (action.get("text") or ""):
            mw.reject("banned_word", "命中敏感词")
        return None

    state = _state(_plugin("guard", [{"stage": "outbound", "handler": "guard", "match": {"sources": ["any_plugin"]}}], {"guard": guard}))
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "含违禁词"}, {"type": "send_message", "text": "正常"}], source_plugin_key="g")
    assert out[0]["_tp_reject"]["error_code"] == middleware.REASON_REJECTED
    assert "banned_word" in out[0]["_tp_reject"]["error"]
    assert "_tp_reject" not in out[1]

    sent: list[str] = []
    rejected: list[str] = []

    async def on_send(action):
        sent.append(action["text"])
        return True

    async def on_rejected(action):
        rejected.append(action["_tp_reject"]["error_code"])
        return False

    batch = await run_action_batch(out, ActionHandlers(on_send_message=on_send, on_rejected=on_rejected))
    assert sent == ["正常"] and rejected == [middleware.REASON_REJECTED] and batch.failed == 1


@pytest.mark.asyncio
async def test_outbound_chain_length_capped_per_account() -> None:
    plugins = [_signature_plugin(f"s{i:02d}", order=10 + i) for i in range(10)]
    state = _state(*plugins)
    chains = middleware.chains_for(state)
    assert len(chains.outbound) == middleware.MAX_CHAIN_LENGTH
    assert len(chains.dropped) == 2
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "x"}], source_plugin_key="z")
    assert out[0]["text"].count("—sig") == middleware.MAX_CHAIN_LENGTH
    # 缓存：同一运行态第二次不重建
    assert middleware.chains_for(state) is chains


@pytest.mark.asyncio
async def test_outbound_chain_budget_skips_remaining(monkeypatch) -> None:
    monkeypatch.setattr(middleware, "CHAIN_BUDGET_MS", 30)

    async def slowish(self, ctx, action, mw):
        await asyncio.sleep(0.04)
        return None

    state = _state(
        _plugin("p1", [{"stage": "outbound", "handler": "slowish", "order": 1}], {"slowish": slowish}),
        _signature_plugin("p2", sources=["any_plugin"], order=2),
    )
    out = await middleware.run_outbound(state, [{"type": "send_message", "text": "x"}], source_plugin_key="p1")
    assert out[0]["text"] == "x"
    assert _stat("p2", "middleware.outbound")["skipped"] == 1


# ─────────────────────────────── render_profile / core.* ───────────────────────────────
@pytest.mark.asyncio
async def test_finalize_middleware_render_profile_runs_before_core() -> None:
    async def make_compact(self, ctx, action, mw):
        action["render_profile"] = {"table": {"compact": True}}
        return action

    state = _state(_plugin("rp", [{"stage": "outbound", "handler": "make_compact"}], {"make_compact": make_compact}))
    out = await loader_mod._finalize_plugin_actions(
        state,
        [{"type": "send_rich_message", "chat_id": -1, "rich_message": {"html": "<table><tr><td>1</td></tr></table>"}}],
        plugin_key="rp",
        default_send_via=None,
    )
    assert out[0]["rich_message"]["html"] == "<table compact><tr><td>1</td></tr></table>"
    assert "render_profile" not in out[0] and "compact_tables" not in out[0]


@pytest.mark.asyncio
async def test_manifest_render_profile_explicit_off_survives_transport() -> None:
    plain = _plugin("plain", [], {}, render={"table": {"bordered": False, "compact": True}})
    state = _state(plain)
    out = await loader_mod._finalize_plugin_actions(
        state,
        [
            {"type": "send_rich_message", "chat_id": -1, "rich_message": {"blocks": [{"type": "table", "cells": [[{"text": "a"}]]}]}},
            {"type": "send_rich_message", "chat_id": -1, "rich_message": {"html": "<table><tr><td>1</td></tr></table>"}},
            {"type": "send_rich_message", "chat_id": -1, "rich_message": {"markdown": "| a |\n|---|\n| 1 |"}},
        ],
        plugin_key="plain",
        default_send_via=None,
    )
    sent = [account_bot_service._with_default_table_style({"rich_message": item["rich_message"]})["rich_message"] for item in out]
    assert sent[0]["blocks"][0] == {"type": "table", "cells": [[{"text": "a"}]], "is_bordered": False, "is_compact": True, "is_striped": True}
    # bordered="false" 在出口按“显式关闭”去掉：Telegram 收到的表格不带 bordered
    assert sent[1]["html"] == "<table compact striped><tr><td>1</td></tr></table>"
    assert sent[2]["markdown"].startswith("<table striped compact>")


def test_render_profile_normalization() -> None:
    assert render_profile.normalize_render_profile({"table": {"compact": True, "x": 1}}) == {"table": {"compact": True}}
    assert render_profile.merge_render_profiles({"table": {"compact": True}}, {"table": {"compact": False, "striped": False}}) == {
        "table": {"compact": False, "striped": False}
    }
    action = {"compact_tables": True, "render_profile": {"table": {"bordered": False}}}
    render_profile.absorb_legacy_compact(action)
    assert action == {"render_profile": {"table": {"compact": True, "bordered": False}}}
    assert {item["name"] for item in render_profile.core_middleware_catalog()} >= {
        "core.rich_table_defaults",
        "core.rich_compact",
        "core.html_fallback",
        "core.guest_merge",
    }


def test_rich_compact_only_called_from_core_module() -> None:
    import pathlib

    root = pathlib.Path(render_profile.__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name in {"rich_compact.py", "render_profile.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "rich_compact import" in text or "from .rich_compact" in text or "services.rich_compact" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


# ─────────────────────────────── inbound ───────────────────────────────
def _inbound_plugin(key: str, func: Any, *, events: list[str] | None = None) -> Plugin:
    return _plugin(key, [{"stage": "inbound", "handler": "on_inbound", "match": {"events": events or ["message"]}}], {"on_inbound": func})


@pytest.mark.asyncio
async def test_inbound_patch_and_annotations() -> None:
    async def normalize(self, ctx, event, mw):
        return {"text": event["message"]["text"].strip().lower(), "annotations": {"lang": "zh"}}

    state = _state(_inbound_plugin("norm", normalize))
    payload = {"event_type": "message", "chat_id": -1, "message": {"text": "  HeLLo "}, "source": {"text": "  HeLLo ", "chat_id": -1}}
    result = await middleware.run_inbound(state, payload, event_type="message", source="userbot")
    assert result.dropped is None and result.changed
    assert result.payload["message"]["text"] == "hello" and result.payload["source"]["text"] == "hello"
    assert result.payload["annotations"] == {"norm": {"lang": "zh"}}
    assert payload["message"]["text"] == "  HeLLo "


@pytest.mark.asyncio
async def test_inbound_drop_and_violation() -> None:
    async def guard(self, ctx, event, mw):
        if "spam" in event["message"]["text"]:
            mw.drop("spam")
        return None

    async def hijack(self, ctx, event, mw):
        return {"chat_id": 1}

    state = _state(_inbound_plugin("guard", guard), _inbound_plugin("hijack", hijack))
    dropped = await middleware.run_inbound(state, {"message": {"text": "buy spam"}}, event_type="message", source="userbot")
    assert dropped.dropped == "spam" and dropped.dropped_by == "guard"
    kept = await middleware.run_inbound(state, {"message": {"text": "hi"}, "chat_id": -1}, event_type="message", source="userbot")
    assert kept.dropped is None and kept.payload["chat_id"] == -1
    assert _stat("hijack", "middleware.inbound")["fail"] == 1  # 第一条被 guard 丢弃，hijack 没机会
    # 事件类型不匹配时不调用
    other = await middleware.run_inbound(state, {"message": {"text": "spam"}}, event_type="callback_query", source="userbot")
    assert other.dropped is None


@pytest.mark.asyncio
async def test_invoke_interaction_entry_runs_inbound_once_and_outbound() -> None:
    seen: list[str] = []
    inbound_calls: list[int] = []

    async def on_inbound(self, ctx, event, mw):
        inbound_calls.append(1)
        if "drop" in event.get("message_text", ""):
            mw.drop("blocked")
        return {"text": event["message_text"].upper()}

    class Echo(Plugin):
        key = "echo"
        display_name = "echo"
        _manifest = Manifest(key="echo", display_name="echo")

        async def on_event(self, ctx, payload):
            seen.append(payload["message_text"])
            return [{"type": "send_message", "chat_id": -1, "text": payload["message_text"]}]

    state = _state(_inbound_plugin("up", on_inbound), _signature_plugin("sig"), account_id=902)
    state.instances["echo"] = Echo()
    state.contexts["echo"] = PluginContext(account_id=902, feature_key="echo")
    loader_mod._STATES[902] = state
    try:
        payload = {"trace_id": "t-1", "event_type": "message", "message_text": "hi", "trigger": {"dispatch_mode": "event_subscription"}}
        actions = await loader_mod.invoke_interaction_entry(902, plugin_key="echo", entry_key="", payload=payload)
        again = await loader_mod.invoke_interaction_entry(902, plugin_key="echo", entry_key="", payload=dict(payload))
        dropped = await loader_mod.invoke_interaction_entry(
            902, plugin_key="echo", entry_key="", payload={**payload, "trace_id": "t-2", "message_text": "drop me"}
        )
    finally:
        loader_mod._STATES.pop(902, None)
    assert seen == ["HI", "HI"]
    assert len(inbound_calls) == 2  # t-1 只跑一次（缓存），t-2 一次
    assert actions[0]["text"] == "HI —sig" and again[0]["text"] == "HI —sig"
    assert dropped == []


# ─────────────────────────────── delivered ───────────────────────────────
@pytest.mark.asyncio
async def test_delivered_runs_async_in_worker_batch() -> None:
    received: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def on_delivered(self, ctx, result, mw):
        received.append(dict(result))
        done.set()

    state = _state(
        _plugin("audit", [{"stage": "delivered", "handler": "on_delivered", "match": {"sources": ["any_plugin"]}}], {"on_delivered": on_delivered})
    )
    out = await middleware.run_outbound(state, [{"type": "send_message", "chat_id": -5, "text": "x", "context": {"plugin_key": "game"}}], source_plugin_key="game")
    assert middleware.wants_delivered(out[0])
    summaries: list[dict[str, Any]] = []

    async def on_send(action):
        return True

    async def after(action, ok):
        if middleware.wants_delivered(action):
            summaries.append(middleware.delivered_summary(action, ok=ok))

    await run_action_batch(out, ActionHandlers(on_send_message=on_send), after_action=after)
    task = middleware.schedule_delivered(state, summaries)
    assert task is not None
    await asyncio.wait_for(done.wait(), 1)
    assert received[0]["ok"] is True and received[0]["chat_id"] == -5 and received[0]["source_plugin_key"] == "game"
    assert "text" not in received[0]


@pytest.mark.asyncio
async def test_delivery_executor_notifies_worker_for_delivered(monkeypatch) -> None:
    published: list[tuple[str, str]] = []

    class _Redis:
        async def publish(self, channel, message):
            published.append((channel, message))

    monkeypatch.setattr(account_bot_service, "send_message", AsyncMock(return_value={"message_id": 11}))
    monkeypatch.setattr("app.services.interaction.delivery.record_action", AsyncMock())
    incoming = SimpleNamespace(account_id=55, chat_id=-100, token="t", guest_reply=None, message_id=1, user_id=2, chat_type="group")
    executor = InteractionDeliveryExecutor(
        incoming=incoming,
        write_log=AsyncMock(),
        run_worker_action=AsyncMock(),
        log_context=lambda _i: {},
        trace_context=lambda _p: {},
        get_redis_client=lambda: _Redis(),
    )
    await executor._notify_delivered([{"action_type": "send_message", "ok": True}])
    assert published and published[0][0].endswith("55") or "55" in published[0][0]
    assert "middleware_delivered" in published[0][1]


# ─────────────────────────────── 访客合并（core.guest_merge） ───────────────────────────────
@pytest.mark.asyncio
async def test_guest_merge_receives_middleware_output(monkeypatch) -> None:
    state = _state(_signature_plugin())
    actions = await loader_mod._finalize_plugin_actions(
        state,
        [{"type": "send_message", "text": "<b>结果</b>", "parse_mode": "html"}],
        plugin_key="game",
        default_send_via=None,
    )
    update = {
        "update_id": 1,
        "guest_message": {
            "message_id": 77,
            "guest_query_id": "gq-1",
            "date": 1,
            "text": "@bot hi",
            "from": {"id": 4242, "first_name": "G"},
            "chat": {"id": -100555, "type": "supergroup"},
        },
    }
    incoming = account_bot_runtime._extract_incoming(1, "tok", update)
    incoming.guest_reply = GuestReplyBuffer(query_id="gq-1")
    monkeypatch.setattr("app.services.interaction.delivery.record_action", AsyncMock())
    executor = InteractionDeliveryExecutor(
        incoming=incoming,
        write_log=AsyncMock(),
        run_worker_action=AsyncMock(side_effect=AssertionError("no userbot")),
        log_context=lambda _i: {},
        trace_context=lambda _p: {},
    )
    await executor.apply(actions)
    assert [(item.kind, item.content) for item in incoming.guest_reply.entries] == [("html", "<b>结果</b> —sig")]


# ─────────────────────────────── 流式中间态 ───────────────────────────────
@pytest.mark.asyncio
async def test_stream_preview_only_runs_declared_middleware() -> None:
    async def mask(self, ctx, action, mw):
        action["text"] = action["text"].replace("坏", "*")
        return action

    async def guard(self, ctx, action, mw):
        if "停" in action["text"]:
            mw.reject("stop")
        return None

    state = _state(
        _plugin("mask", [{"stage": "outbound", "handler": "mask", "order": 1, "match": {"action_types": ["stream_preview", "send_message"], "sources": ["any_plugin"]}}], {"mask": mask}),
        _plugin("guard", [{"stage": "outbound", "handler": "guard", "order": 2, "match": {"action_types": ["stream_preview"], "sources": ["any_plugin"]}}], {"guard": guard}),
        _signature_plugin("sig"),
    )
    assert await middleware.run_stream_preview(state, "坏人", source_plugin_key="ai") == "*人"
    assert await middleware.run_stream_preview(state, "停下", source_plugin_key="ai") is None


@pytest.mark.asyncio
async def test_stream_editor_applies_preview_filter(monkeypatch) -> None:
    from app.worker.plugins.stream_edit import PluginStreamEditor

    pushed: list[str] = []
    monkeypatch.setattr(account_bot_service, "call_bot_api", AsyncMock(side_effect=lambda *a, **k: pushed.append(a[2]["text"])))

    async def token():
        return "tok"

    async def flt(text, chat_id):
        return None if "skip" in text else text.upper()

    editor = PluginStreamEditor(apply=AsyncMock(return_value={"ok": True}), token_provider=token, chat_id=-1, message_id=3, preview_filter=flt)
    await editor._push("hello")
    await editor._push("skip me")
    assert pushed == ["HELLO"]
    assert editor.stats["preview_rejected"] == 1


# ─────────────────────────────── 阻塞检测 ───────────────────────────────
@pytest.mark.asyncio
async def test_blocking_probe_records_sync_steps() -> None:
    hits: list[float] = []

    async def work():
        await asyncio.sleep(0)
        time.sleep(0.22)
        await asyncio.sleep(0)
        return 42

    probe = blocking_monitor.BlockingProbe(work(), hits.append)
    assert await probe == 42
    assert len(hits) == 1 and hits[0] >= 200


@pytest.mark.asyncio
async def test_blocking_probe_propagates_cancel_and_errors() -> None:
    async def forever():
        await asyncio.sleep(10)

    task = asyncio.ensure_future(blocking_monitor.BlockingProbe(forever(), lambda ms: None))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def bad():
        await asyncio.sleep(0)
        raise ValueError("x")

    with pytest.raises(ValueError):
        await blocking_monitor.BlockingProbe(bad(), lambda ms: None)


@pytest.mark.asyncio
async def test_invoke_plugin_guarded_records_blocking_ms() -> None:
    state = loader_mod._AccountState(31)

    async def handler():
        time.sleep(0.21)
        return "ok"

    result = await loader_mod._invoke_plugin_with_resilience(state, "heavy", handler, hook="on_event", event_type="message")
    assert result == "ok"
    row = plugin_stats.get_recorder().local_snapshot("heavy")["hooks"][0]
    assert row["blocked"] == 1 and row["blocking_ms"] >= 200 and row["calls"] == 1


def test_stats_parse_includes_blocking_fields() -> None:
    parsed = plugin_stats.parse_stats_hash({"1|on_event|message|calls": "2", "1|on_event|message|blocked": "1", "1|on_event|message|blocking_ms": "250"})
    assert parsed["hooks"][0]["blocked"] == 1 and parsed["hooks"][0]["blocking_ms"] == 250
    assert parsed["totals"]["blocking_ms"] == 250


# ─────────────────────────────── 能力 / API ───────────────────────────────
def test_capabilities_catalog_lists_middleware() -> None:
    from app.worker.plugins.capabilities import CAPABILITY_INDEX, PluginCapabilities
    from app.worker.plugins.message_ops import BufferedMessageOps
    from app.worker.plugins.versioning import PLUGIN_API_VERSION

    assert tuple(int(part) for part in PLUGIN_API_VERSION.split(".")) >= (1, 2)
    for name in ("middleware.outbound", "middleware.inbound", "middleware.delivered", "middleware.stream_preview", "ai.stream_extra_body", "runtime.blocking_monitor"):
        assert name in CAPABILITY_INDEX and CAPABILITY_INDEX[name].plugin_api == "1.2"
        assert PluginCapabilities().has(name)
    assert PluginCapabilities(BufferedMessageOps()).has("messages.render_profile")


@pytest.mark.asyncio
async def test_capabilities_api_lists_host_resources_and_middleware() -> None:
    from app.api.plugins import get_plugin_capabilities

    response = await get_plugin_capabilities(SimpleNamespace())
    refs = {item.ref for item in response.host_resources}
    assert "ai.models@1" in refs
    assert {item.name for item in response.core_middleware} >= {"core.rich_compact", "core.guest_merge"}
    assert response.middleware_limits is not None and response.middleware_limits.max_chain_length == 8
    assert response.middleware_limits.default_timeout_ms == 250
