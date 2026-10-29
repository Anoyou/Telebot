"""插件平台化 P1（0.108.0）：回调 token、统一 MessageOps、ctx.host.read、bot_api_extra / extra_body、
钩子 Trace 与熔断同口径、历史插件兼容表。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import plugin_compat
from app.services import llm_extra_body
from app.services.event_bus import dispatch_event, normalize_event_subscription
from app.services.interaction import action_extensions as ext
from app.services.interaction.action_core import ActionHandlers, run_action_batch
from app.services.interaction.guest_reply import sanitize_guest_reply_markup
from app.services.llm_client import LLMError, apply_plugin_extra_body
from app.worker.plugins import capabilities as caps_mod
from app.worker.plugins import deprecations as dep_mod
from app.worker.plugins import loader, plugin_stats
from app.worker.plugins.base import PluginContext
from app.worker.plugins.host_read import HostReadError, PluginHost, validate_reads
from app.worker.plugins.message_ops import LIVE_ACTION_METHODS, BufferedMessageOps
from app.worker.plugins.versioning import PLUGIN_API_VERSION


class _KV:
    def __init__(self, *, broken: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.broken = broken

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        if self.broken:
            raise ConnectionError("redis down")
        self.data[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        if self.broken:
            raise ConnectionError("redis down")
        return self.data.get(key)


def _callback_event(*, chat_id: int | None = -100, data: str = "x") -> dict[str, Any]:
    return {
        "source": {"type": "callback_query", "channel": "interaction_bot", "chat_id": chat_id, "callback_data": data},
        "event_type": "callback_query",
        "message": {"chat_id": chat_id},
        "chat": {"id": chat_id, "type": "supergroup"},
        "sender": {"user_id": 2001},
    }


# ─────────────────────────────────────────────────────
# 回调 token
# ─────────────────────────────────────────────────────
async def test_mint_and_resolve_callback_token_roundtrip() -> None:
    redis = _KV()
    token = await ext.mint_callback_token(
        redis, account_id=7, plugin_key="demo", data="retry:42", origin={"kind": "chat", "chat_id": -1}, ttl_seconds=5
    )
    assert token.startswith("cb1.") and len(token.encode()) <= 64
    assert list(redis.ttl.values()) == [ext.CALLBACK_TOKEN_MIN_TTL_SECONDS]
    record = await ext.resolve_callback_token(redis, account_id=7, callback_data=token)
    assert record is not None
    assert record["plugin_key"] == "demo" and record["data"] == "retry:42" and record["token"] == token
    # 其它账号拿不到
    assert await ext.resolve_callback_token(redis, account_id=8, callback_data=token) is None
    assert await ext.resolve_callback_token(redis, account_id=7, callback_data="retry:42") is None
    with pytest.raises(ext.CallbackTokenUnavailable):
        await ext.resolve_callback_token(_KV(broken=True), account_id=7, callback_data=token)


async def test_preprocess_rewrites_callback_buttons_and_keeps_others() -> None:
    redis = _KV()
    action = {
        "type": "send_message",
        "chat_id": -100,
        "text": "hi",
        "context": {"plugin_key": "demo"},
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "重试", "callback": {"data": "retry:1"}}, {"text": "官网", "url": "https://example.com"}],
                [{"text": "旧写法", "callback_data": "legacy"}],
            ]
        },
    }
    out = await ext.preprocess_plugin_action(action, account_id=3, redis_factory=lambda: redis)
    rows = out["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["callback_data"].startswith("cb1.") and "callback" not in rows[0][0]
    assert rows[0][1] == {"text": "官网", "url": "https://example.com"}
    assert rows[1][0]["callback_data"] == "legacy"
    assert out["callback_tokens"] == 1
    stored = json.loads(next(iter(redis.data.values())))
    assert stored["origin"] == {"kind": "chat", "chat_id": -100}
    # 幂等：再处理一次不会重复签发
    again = await ext.preprocess_plugin_action(out, account_id=3, redis_factory=lambda: redis)
    assert again["callback_tokens"] == 1 and len(redis.data) == 1


async def test_preprocess_guest_and_inline_results_and_redis_failure() -> None:
    redis = _KV()
    guest = {
        "type": "answer_guest_query",
        "guest_query_id": "g1",
        "context": {"plugin_key": "demo"},
        "reply_markup": {"inline_keyboard": [[{"text": "重试", "callback": {"data": {"q": 1}}}]]},
    }
    out = await ext.preprocess_plugin_action(guest, account_id=1, redis_factory=lambda: redis)
    assert ext.is_callback_token(out["reply_markup"]["inline_keyboard"][0][0]["callback_data"])
    assert json.loads(next(iter(redis.data.values())))["origin"]["kind"] == "guest"

    inline = {
        "type": "answer_inline_query",
        "inline_query_id": "iq",
        "context": {"plugin_key": "demo"},
        "results": [{"id": "1", "reply_markup": {"inline_keyboard": [[{"text": "a", "callback": {"data": "a"}}]]}}],
    }
    out = await ext.preprocess_plugin_action(inline, account_id=1, redis_factory=lambda: redis)
    assert ext.is_callback_token(out["results"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"])

    # Redis 不可用：签发失败的按钮去掉，消息照常发出，并带警告
    down = {
        "type": "send_message",
        "chat_id": 1,
        "text": "t",
        "context": {"plugin_key": "demo"},
        "reply_markup": {"inline_keyboard": [[{"text": "a", "callback": {"data": "a"}}]]},
    }
    out = await ext.preprocess_plugin_action(down, account_id=1, redis_factory=lambda: _KV(broken=True))
    assert "reply_markup" not in out
    assert out["callback_token_warnings"] == [ext.CALLBACK_REASON_UNAVAILABLE]
    assert ext.action_rejection(out) is None

    # 没有插件归属的 callback 按钮拒绝
    orphan = {"type": "send_message", "text": "t", "reply_markup": {"inline_keyboard": [[{"text": "a", "callback": {"data": "a"}}]]}}
    out = await ext.preprocess_plugin_action(orphan, account_id=1, redis_factory=lambda: redis)
    assert ext.action_rejection(out)["error_code"] == "callback_owner_unknown"


def test_raw_callback_data_is_registered_deprecation(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "markup.raw_callback_data" in dep_mod.DEPRECATION_INDEX
    assert "payload.admin_user_ids" in dep_mod.DEPRECATION_INDEX


def test_own_buttons_scope_routes_only_to_token_owner() -> None:
    own = normalize_event_subscription(
        {"source": ["interaction_bot"], "events": ["callback_query"], "scope": "own_buttons"},
        plugin_key="owner",
        entry_key="main",
    )
    other = normalize_event_subscription(
        {"source": ["interaction_bot"], "events": ["callback_query"], "scope": "all_allowed_chats"},
        plugin_key="other",
        entry_key="main",
    )
    assert own.scope == "own_buttons"
    # 会话不在允许名单（chat scope 不满足）也能投递给 own_buttons
    state = {"allowed_chat_ids": [-999], "callback_owner_plugin_key": "owner"}
    decisions = {d.plugin_key: d for d in dispatch_event(_callback_event(), [own, other], state).decisions}
    assert decisions["owner"].matched is True
    assert decisions["other"].matched is False and decisions["other"].reason_code == "callback_not_owned"
    # inline 消息上的按钮（没有 chat）
    inline = dispatch_event(_callback_event(chat_id=None), [own], {"callback_owner_plugin_key": "owner"}).decisions[0]
    assert inline.matched is True
    # 不是 token 的点击：own_buttons 不接收，其它订阅照旧
    plain = {d.plugin_key: d for d in dispatch_event(_callback_event(), [own, other], {"allowed_chat_ids": [-100]}).decisions}
    assert plain["owner"].matched is False and plain["other"].matched is True


async def test_runtime_resolves_token_into_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import account_bot_runtime as rt

    redis = _KV()
    token = await ext.mint_callback_token(redis, account_id=5, plugin_key="demo", data="retry:9", origin={"kind": "inline"})
    monkeypatch.setattr(rt, "get_redis", lambda: redis)
    answered: list[dict[str, Any]] = []

    async def _answer(incoming: Any, **kwargs: Any) -> None:
        answered.append(kwargs)

    async def _span(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(rt, "_answer_callback", _answer)
    monkeypatch.setattr(rt, "record_span", _span)
    incoming = SimpleNamespace(
        account_id=5,
        callback_id="cq1",
        callback_data=token,
        trace_id="t",
        user_id=42,
        display_name="A",
        username="a",
        chat_id=None,
        message_id=None,
        inline_message_id="im1",
    )
    event = {"source": {"callback_data": token}, "raw": {"callback_data": token}, "inline_callback": {"data": token}}
    owner, handled = await rt._resolve_event_bus_callback_token(incoming, event)
    assert owner == "demo" and handled is None
    assert event["callback"]["data"] == "retry:9" and event["callback"]["clicker"]["user_id"] == 42
    assert event["source"]["callback_data"] == "retry:9" and event["inline_callback"]["data"] == "retry:9"
    # 过期 → 答复“按钮已过期”并结束
    incoming.callback_data = "cb1.missing"
    owner, handled = await rt._resolve_event_bus_callback_token(incoming, {"source": {}})
    assert owner is None and handled == (True, True) and answered[-1]["text"] == "按钮已过期"
    # Redis 不可用 → fail-closed
    monkeypatch.setattr(rt, "get_redis", lambda: _KV(broken=True))
    owner, handled = await rt._resolve_event_bus_callback_token(incoming, {"source": {}})
    assert handled == (True, False) and answered[-1]["show_alert"] is True


def test_guest_reply_keeps_platform_tokens() -> None:
    markup = {
        "inline_keyboard": [
            [{"text": "token", "callback_data": "cb1.abc"}, {"text": "raw", "callback_data": "raw"}],
            [{"text": "url", "url": "https://e.x"}],
        ]
    }
    out = sanitize_guest_reply_markup(markup)
    assert out == {"inline_keyboard": [[{"text": "token", "callback_data": "cb1.abc"}], [{"text": "url", "url": "https://e.x"}]]}


# ─────────────────────────────────────────────────────
# bot_api_extra
# ─────────────────────────────────────────────────────
def test_validate_bot_api_extra() -> None:
    values, error = ext.validate_bot_api_extra("send_message", {"disable_notification": True})
    assert values == {"disable_notification": True} and error is None
    _, error = ext.validate_bot_api_extra("send_message", {"chat_id": 1})
    assert error and "平台字段" in error
    _, error = ext.validate_bot_api_extra("send_message", {"foo": 1})
    assert error and "foo" in error
    _, error = ext.validate_bot_api_extra("payout", {"disable_notification": True})
    assert error and "不支持" in error


async def test_bot_api_extra_scoped_to_action_method_and_rejection() -> None:
    seen: list[tuple[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    async def _send(action: dict[str, Any]) -> bool:
        seen.append(("sendMessage", ext.merge_bot_api_extra_json("sendMessage", {"chat_id": 1, "protect_content": False})))
        seen.append(("pinChatMessage", ext.bot_api_extra_for_method("pinChatMessage")))
        return True

    async def _reject(action: dict[str, Any]) -> bool:
        rejected.append(action)
        return False

    async def _pre(action: dict[str, Any]) -> dict[str, Any]:
        return await ext.preprocess_plugin_action(action, account_id=1, redis_factory=lambda: None, plugin_key="demo")

    actions = [
        {"type": "send_message", "chat_id": 1, "text": "a", "bot_api_extra": {"disable_notification": True, "protect_content": True}},
        {"type": "send_message", "chat_id": 1, "text": "b", "bot_api_extra": {"reply_markup": {}}},
    ]
    batch = await run_action_batch(
        actions, ActionHandlers(on_send_message=_send, on_rejected=_reject), preprocess=_pre
    )
    assert batch.executed == 2 and batch.failed == 1
    # 平台字段优先（protect_content=False 保留），透传只补缺；其它方法拿不到
    assert seen[0] == ("sendMessage", {"chat_id": 1, "protect_content": False, "disable_notification": True})
    assert seen[1] == ("pinChatMessage", None)
    assert ext.bot_api_extra_for_method("sendMessage") is None
    assert rejected[0]["_tp_reject"]["error_code"] == ext.BOT_API_EXTRA_REJECTED
    assert rejected[0]["bot_api_extra"] == ["reply_markup"]


def test_trace_snapshot_omits_bot_api_extra_values() -> None:
    from app.services.event_trace import redact_payload_snapshot

    snap = redact_payload_snapshot({"action": {"_tp_bot_api_extra": {"message_effect_id": "secret-ish"}}})
    assert snap["action"]["_tp_bot_api_extra"] == ["message_effect_id"]


async def test_bot_api_request_merges_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import account_bot_service as svc

    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200
        content = b"1"
        headers: dict[str, str] = {}

        def json(self) -> dict[str, Any]:
            return {"ok": True, "result": {"message_id": 1}}

    class _Client:
        async def post(self, url: str, **kwargs: Any) -> _Resp:
            captured.update(kwargs)
            return _Resp()

    async def _client() -> _Client:
        return _Client()

    monkeypatch.setattr(svc, "get_bot_api_client", _client)
    token = ext._ACTIVE_BOT_API_EXTRA.set((frozenset({"sendMessage"}), {"disable_notification": True}))
    try:
        await svc._bot_api_request("123:abc", "sendMessage", json_payload={"chat_id": 1, "text": "x"}, rate_limit_wait_budget=0)
    finally:
        ext._ACTIVE_BOT_API_EXTRA.reset(token)
    assert captured["json"] == {"chat_id": 1, "text": "x", "disable_notification": True}


# ─────────────────────────────────────────────────────
# extra_body
# ─────────────────────────────────────────────────────
def test_extra_body_validation_and_format_check() -> None:
    assert llm_extra_body.validate_extra_body({"reasoning_effort": "low"}) == {"reasoning_effort": "low"}
    with pytest.raises(llm_extra_body.ExtraBodyRejected, match="平台字段"):
        llm_extra_body.validate_extra_body({"model": "x"})
    with pytest.raises(llm_extra_body.ExtraBodyRejected, match="白名单"):
        llm_extra_body.validate_extra_body({"nope": 1})
    body: dict[str, Any] = {"model": "m"}
    token = llm_extra_body.activate({"reasoning_effort": "low"})
    try:
        apply_plugin_extra_body(body, "chat_completions")
        assert body["reasoning_effort"] == "low"
        with pytest.raises(LLMError, match="不支持 extra_body"):
            apply_plugin_extra_body({}, "anthropic")
    finally:
        llm_extra_body.reset(token)
    untouched: dict[str, Any] = {}
    apply_plugin_extra_body(untouched, "anthropic")
    assert untouched == {}


async def test_ai_complete_rejects_unknown_extra_body() -> None:
    from app.worker.plugins.ai_facade import AIUnavailableError, PluginAI

    ai = PluginAI(account_id=1, plugin_key="demo")
    with pytest.raises(AIUnavailableError, match="白名单"):
        await ai.complete("s", "u", extra_body={"nope": True})


# ─────────────────────────────────────────────────────
# ctx.host.read
# ─────────────────────────────────────────────────────
async def test_plugin_host_requires_declaration() -> None:
    async def _admins() -> dict[str, Any]:
        return {"owner_user_ids": [1], "admin_user_ids": [1, 2]}

    host = PluginHost(plugin_key="demo", account_id=1, declared=["account.admins@1", "bogus@1"], resolvers={"account.admins@1": _admins})
    assert host.declared == ["account.admins@1"]
    assert await host.read("account.admins@1") == {"owner_user_ids": [1], "admin_user_ids": [1, 2]}
    with pytest.raises(HostReadError, match="未在 plugin.json reads 中声明"):
        await host.read("chat.allowed@1")
    with pytest.raises(HostReadError, match="未知宿主资源"):
        await host.read("secrets@1")
    with pytest.raises(HostReadError, match="不支持参数"):
        await host.read("account.admins@1", foo=1)
    assert validate_reads(["ai.models@1", "x@9"]) == (["ai.models@1"], ["x@9"])


async def test_loader_builds_host_with_state(monkeypatch: pytest.MonkeyPatch) -> None:
    state = loader._AccountState(9)
    state.owner_tg_user_id = 100
    state.sudo_users = {200: {}}
    state.ignored_peers = {-5, -3}
    manifest = SimpleNamespace(reads=["account.admins@1", "chat.allowed@1", "ai.models@1"])
    provider = SimpleNamespace(
        id=1, name="P", provider="openai", default_model="m1", tags=["fast"],
        models=[{"id": "m1"}, {"id": "m2", "enabled": False}],
    )

    class _AI:
        async def list_providers(self) -> list[Any]:
            return [provider]

    host = loader._build_plugin_host(state, "demo", manifest, _AI(), None)
    assert await host.read("account.admins@1") == {"owner_user_ids": [100], "admin_user_ids": [100, 200]}
    assert await host.read("chat.allowed@1") == {"all": False, "chat_ids": [-5, -3]}
    models = await host.read("ai.models@1")
    assert [item["model"] for item in models] == ["m1"] and models[0]["default"] is True
    assert len(await host.read("ai.models@1", enabled_only=False)) == 2
    no_ai = loader._build_plugin_host(state, "demo", manifest, None, None)
    with pytest.raises(HostReadError, match="当前上下文不提供"):
        await no_ai.read("ai.models@1")
    assert PluginContext(account_id=1, feature_key="x").host is None


def test_manifest_reads_merged_from_plugin_json(tmp_path: Path) -> None:
    from app.worker.plugins.manifest import Manifest

    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": "demo", "reads": ["account.admins@1"]}), encoding="utf-8")
    manifest = Manifest(key="demo", display_name="Demo")
    loader._merge_static_plugin_api(plugin_dir, manifest)
    assert manifest.reads == ["account.admins@1"]
    assert manifest.to_dict()["reads"] == ["account.admins@1"]


# ─────────────────────────────────────────────────────
# 统一 MessageOps
# ─────────────────────────────────────────────────────
async def test_live_facade_has_every_builder_and_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LIVE_ACTION_METHODS:
        assert callable(getattr(loader._LiveMessageOps, name)), name
        assert callable(getattr(BufferedMessageOps, name)), name
    live = loader._LiveMessageOps(loader._AccountState(1), plugin_key="demo")
    applied: list[list[dict[str, Any]]] = []

    async def _apply(actions: list[dict[str, Any]], *, entry_key: str | None = None) -> dict[str, Any]:
        applied.append(actions)
        if actions[0]["type"] == "answer_guest_query":
            return {"ok": True, "inline_message_id": "im-9"}
        return {"ok": False, "errors": [{"index": 0, "error_code": "telegram_api_error", "error": "boom"}]}

    monkeypatch.setattr(live, "apply", _apply)
    photo = await live.send_photo(chat_id=1, photo=b"\x89PNG", caption="c", bot_api_extra={"has_spoiler": True})
    assert applied[-1][0]["type"] == "send_photo" and applied[-1][0]["bot_api_extra"] == {"has_spoiler": True}
    assert photo["errors"][0]["error"] == "boom"
    await live.edit_rich(chat_id=1, message_id=2, markdown="**x**")
    assert applied[-1][0]["rich_message"] == {"markdown": "**x**"}
    await live.edit_caption(chat_id=1, message_id=2, caption="new")
    assert applied[-1][0]["type"] == "edit_caption"
    guest = await live.answer_guest_query(guest_query_id="g", text="t")
    assert guest["inline_message_id"] == "im-9"


def test_new_capabilities_registered() -> None:
    assert PLUGIN_API_VERSION >= "1.1"
    names = {item.name: item for item in caps_mod.CAPABILITIES}
    for name in (
        "messages.callback_routing",
        "messages.unified_ops",
        "messages.bot_api_extra",
        "ai.extra_body",
        "host.read",
        "host.read.account.admins@1",
        "host.read.ai.models@1",
        "host.read.chat.allowed@1",
        "host.read.plugin.stats@1",
        "runtime.hook_trace",
    ):
        assert names[name].since == caps_mod._P1_RELEASE, name
        assert names[name].plugin_api == "1.1"
    assert names["runtime.capabilities"].since == "0.107.0-beta.1"
    ctx = PluginContext(account_id=1, feature_key="demo", messages=BufferedMessageOps())
    assert ctx.capabilities.has("messages.callback_routing")
    assert ctx.capabilities.has("messages.inline_rich_edit")
    assert ctx.capabilities.has("host.read.account.admins@1")


# ─────────────────────────────────────────────────────
# 钩子 Trace 与熔断
# ─────────────────────────────────────────────────────
def test_update_circuit_consecutive_and_failure_rate() -> None:
    entry = None
    for i in range(2):
        entry = plugin_stats.update_circuit(entry, ok=False, now=float(i), threshold=3, cooldown=60)
    assert entry is not None and entry["opened_until"] == 0.0
    entry = plugin_stats.update_circuit(entry, ok=False, now=2.0, threshold=3, cooldown=60)
    assert entry["opened_until"] == 62.0 and entry["reason"] == "consecutive"
    assert plugin_stats.update_circuit(None, ok=True, now=0.0, threshold=3, cooldown=60) is None

    # 时好时坏：从不连续失败 3 次，但窗口失败率 ≥ 80%
    entry = None
    now = 0.0
    for i in range(30):
        now += 1
        ok = i % 6 == 0
        entry = plugin_stats.update_circuit(entry, ok=ok, now=now, threshold=10, cooldown=30)
    assert entry is not None and entry.get("reason") == "failure_rate" and entry["opened_until"] > now
    assert plugin_stats.reason_code_for_outcome("timeout") == "plugin_timeout"
    assert plugin_stats.reason_code_for_outcome("fail") == "plugin_error"


async def test_invoke_records_trace_span_with_reason_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "plugin_update_in_progress", lambda *args, **kwargs: False)
    spans: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def _span(*args: Any, **kwargs: Any) -> None:
        spans.append((args, kwargs))

    monkeypatch.setattr(loader, "record_span", _span)
    state = loader._AccountState(3)

    async def boom() -> None:
        raise ValueError("bad")

    with pytest.raises(ValueError):
        await loader._invoke_plugin_with_resilience(state, "demo", boom, hook="on_event", event_type="message", trace="tr-1")
    args, kwargs = spans[-1]
    assert args[0] == "tr-1" and args[1] == "plugin_hook"
    assert kwargs["reason_code"] == "plugin_error" and kwargs["hook"] == "on_event" and "duration_ms" in kwargs

    async def ok() -> list[Any]:
        return []

    count = len(spans)
    await loader._invoke_plugin_with_resilience(state, "demo", ok, hook="on_event", event_type="message", trace="tr-1")
    assert len(spans) == count  # 快速成功不记 span
    assert "demo" not in state.plugin_circuits or state.plugin_circuits["demo"]["failures"] == 0


# ─────────────────────────────────────────────────────
# 历史插件兼容表
# ─────────────────────────────────────────────────────
def test_plugin_compat_tables() -> None:
    from app.services import account_bot_service as svc

    assert plugin_compat.normalize_legacy_feature_config("codex_image", {"model": "gpt-5.4"}) == {"model": "gpt-5.5"}
    assert plugin_compat.normalize_legacy_feature_config("other", {"model": "gpt-5.4"}) == {"model": "gpt-5.4"}
    assert plugin_compat.fallback_entry_session_scope("game24", "start_paid_game") == "chat"
    assert plugin_compat.fallback_entry_participant_policy("blackjack", "start_blackjack") == "solo_owner"
    assert plugin_compat.fallback_entry_has_field("pt_promote", "promote_torrent", "prize") is False
    assert plugin_compat.fallback_entry_keys("dice_grid_hunt") == ["answer_dice_grid_hunt", "start_dice_grid_hunt"]
    assert svc.FALLBACK_CHAT_SESSION_MODULE_ENTRIES is plugin_compat.FALLBACK_CHAT_SESSION_MODULE_ENTRIES
    assert svc.DEFAULT_MATH10_START_KEYWORDS == ["发十以内算数", "十以内算数", "开算数题"]
    assert plugin_compat.uses_multi_token_config("chatgpt_image")
    assert loader._missing_plugin_error("codex_image")[0] == "plugin codex_image not installed"
    assert loader._missing_plugin_error("x")[0] == "plugin not found"


def test_no_plugin_key_literals_outside_compat_table() -> None:
    """插件 key 字面量只允许出现在兼容表、API schema 的 Literal 与注释示例里。"""

    import re

    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(
        r'"(math10|game24|codex_image|chatgpt_image|pt_promote|dice_grid_hunt|guess_number|blackjack|auto_reply|autorepeat)"'
    )
    allowed = {
        "plugin_compat.py",
        "schemas/account_bot.py",  # 交互规则 action 的 API 枚举（Literal），改动会影响 OpenAPI
        "services/feature_service.py",  # 仅 docstring 示例
    }
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/") or "/builtin/" in rel or rel in allowed:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == []
