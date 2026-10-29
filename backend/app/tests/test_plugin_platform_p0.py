"""插件平台化 P0：能力注册表、Plugin API 版本、prerelease 版本比较、钩子统计、弃用登记。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.worker.plugins import capabilities as caps_mod
from app.worker.plugins import deprecations as dep_mod
from app.worker.plugins import loader, plugin_stats
from app.worker.plugins.base import PluginContext
from app.worker.plugins.message_ops import BufferedMessageOps
from app.worker.plugins.versioning import (
    PLUGIN_API_VERSION,
    PluginApiRangeError,
    compare_versions,
    host_satisfies_min_version,
    parse_plugin_api_range,
    parse_version,
    plugin_api_satisfies,
)

LEGACY_FLAGS = (
    "inline_rich_edit",
    "live_inline_edit",
    "rich_table_compact",
    "rich_table_default_style",
    "guest_reply_markup",
    "streaming_edit",
)


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def _queue(*args: Any, **kwargs: Any) -> _FakePipeline:
            self._ops.append((name, args, kwargs))
            return self

        return _queue

    async def execute(self) -> list[Any]:
        results = []
        for name, args, kwargs in self._ops:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = str(int(bucket.get(field, "0")) + int(amount))
        return int(bucket[field])

    async def hset(self, key: str, field: str | None = None, value: Any = None, mapping: dict | None = None) -> int:
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            bucket.update({k: str(v) for k, v in mapping.items()})
        if field is not None:
            bucket[field] = str(value)
        return 1

    async def hsetnx(self, key: str, field: str, value: Any) -> int:
        bucket = self.hashes.setdefault(key, {})
        if field in bucket:
            return 0
        bucket[field] = str(value)
        return 1

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.hashes.pop(key, None) is not None else 0


@pytest.fixture(autouse=True)
def _reset_stats_state():
    recorder = plugin_stats.get_recorder()
    recorder.reset_local()
    recorder.set_redis_factory(None)
    dep_mod.reset_warn_state()
    yield
    recorder.reset_local()
    recorder.set_redis_factory(None)
    dep_mod.reset_warn_state()


# ─────────────────────────────────────────────────────
# 版本比较
# ─────────────────────────────────────────────────────
def test_parse_and_compare_prerelease_versions() -> None:
    assert parse_version("v0.106.0-beta.12") is not None
    assert str(parse_version("0.106.0-beta.12")) == "0.106.0-beta.12"
    assert compare_versions("0.106.0-beta.3", "0.106.0-beta.12") < 0
    assert compare_versions("0.106.0-beta.12", "0.106.0") < 0
    assert compare_versions("0.107.0-beta.1", "0.106.0") > 0
    assert compare_versions("0.106.0-alpha.9", "0.106.0-beta.1") < 0
    assert compare_versions("1.0.0-beta", "1.0.0-beta.1") < 0
    assert compare_versions("garbage", "0.1.0") < 0


@pytest.mark.parametrize(
    ("host", "minimum", "expected"),
    [
        ("0.106.0-beta.3", "0.106.0-beta.12", False),
        ("0.106.0-beta.12", "0.106.0-beta.12", True),
        ("0.106.0-beta.17", "0.106.0-beta.12", True),
        ("0.106.0", "0.106.0-beta.12", True),
        ("0.107.0-beta.1", "0.106.0-beta.12", True),
        ("0.105.9", "0.106.0-beta.1", False),
        # 不带 prerelease 的声明保持旧语义：同一 x.y.z 的 beta 宿主视为满足
        ("0.106.0-beta.3", "0.106.0", True),
        ("0.105.0-beta.1", "0.106.0", False),
        # 已发布插件的典型声明
        ("0.106.0-beta.17", "0.72.0-beta.2", True),
        ("0.106.0-beta.17", "0.105.0-beta.1", True),
        ("0.106.0-beta.17", None, True),
        ("0.106.0-beta.17", "not-a-version", True),
    ],
)
def test_host_satisfies_min_version(host: str, minimum: str | None, expected: bool) -> None:
    assert host_satisfies_min_version(host, minimum) is expected


@pytest.mark.parametrize(
    ("spec", "api", "expected"),
    [
        ("^1.0", "1.0", True),
        ("^1.0", "1.4", True),
        ("^1.0", "2.0", False),
        ("1.x", "1.9", True),
        ("1", "2.0", False),
        ("1.2", "1.1", False),
        ("1.2", "1.3", True),
        ("~1.2", "1.3", False),
        (">=1.0,<2", "1.7", True),
        (">= 1.1 < 2", "1.0", False),
        ("*", "9.9", True),
        ("", "1.0", True),
        (">=2", "1.0", False),
    ],
)
def test_plugin_api_ranges(spec: str, api: str, expected: bool) -> None:
    assert plugin_api_satisfies(spec, api) is expected


def test_plugin_api_range_rejects_garbage() -> None:
    with pytest.raises(PluginApiRangeError):
        parse_plugin_api_range("one.two")
    assert PLUGIN_API_VERSION.count(".") == 1


# ─────────────────────────────────────────────────────
# 能力注册表
# ─────────────────────────────────────────────────────
def test_legacy_flags_are_generated_from_single_registry() -> None:
    expected = {
        BufferedMessageOps: caps_mod.legacy_flags_for(caps_mod.FACADE_BUFFERED),
        loader._LiveMessageOps: caps_mod.legacy_flags_for(caps_mod.FACADE_LIVE),
        loader._InteractionEntryMessageOps: caps_mod.legacy_flags_for(caps_mod.FACADE_ENTRY),
    }
    for cls, flags in expected.items():
        for attr in LEGACY_FLAGS:
            assert (getattr(cls, attr, False) is True) is (attr in flags), (cls.__name__, attr)
    # 与 0.106.0-beta.17 的可见行为一致（旧插件 `getattr(..., False) is True` 探测不变）
    for attr in LEGACY_FLAGS:
        assert getattr(loader._LiveMessageOps, attr) is True
        assert getattr(loader._InteractionEntryMessageOps, attr) is True
    assert getattr(BufferedMessageOps, "inline_rich_edit", False) is False
    assert getattr(BufferedMessageOps, "live_inline_edit", False) is False
    assert getattr(BufferedMessageOps, "streaming_edit", False) is False
    for attr in ("rich_table_compact", "rich_table_default_style", "guest_reply_markup"):
        assert getattr(BufferedMessageOps, attr) is True


def test_every_legacy_attr_has_registry_entry() -> None:
    attrs = {item.legacy_attr for item in caps_mod.CAPABILITIES if item.legacy_attr}
    assert attrs == set(LEGACY_FLAGS)
    for item in caps_mod.CAPABILITIES:
        assert parse_version(item.since) is not None, item.name
        assert item.summary


def test_ctx_capabilities_follows_messages_facade() -> None:
    buffered_ctx = PluginContext(account_id=1, feature_key="demo", messages=BufferedMessageOps())
    assert buffered_ctx.capabilities.has("messages.guest_reply_markup")
    assert not buffered_ctx.capabilities.has("messages.live_inline_edit")
    assert buffered_ctx.capabilities.has("runtime.hook_stats")
    assert buffered_ctx.capabilities.version("messages.unknown") == 0
    assert "messages.rich_table_compact" in buffered_ctx.capabilities
    assert buffered_ctx.capabilities.plugin_api == PLUGIN_API_VERSION

    entry_ops = loader._InteractionEntryMessageOps.__new__(loader._InteractionEntryMessageOps)
    entry_ctx = PluginContext(account_id=1, feature_key="demo", messages=entry_ops)
    assert entry_ctx.capabilities.has("messages.live_inline_edit")
    assert not entry_ctx.capabilities.has("messages.live_inline_edit", min_version=2)

    # 非平台 facade（测试替身）退回旧属性探测
    fake_ctx = PluginContext(account_id=1, feature_key="demo", messages=SimpleNamespace(guest_reply_markup=True))
    assert fake_ctx.capabilities.has("messages.guest_reply_markup")
    assert not fake_ctx.capabilities.has("messages.rich_table_compact")
    none_ctx = PluginContext(account_id=1, feature_key="demo")
    assert not none_ctx.capabilities.has("messages.rich_table_compact")
    assert none_ctx.capabilities.has("manifest.plugin_api")


# ─────────────────────────────────────────────────────
# 加载兼容检查
# ─────────────────────────────────────────────────────
def test_manifest_compatible_respects_beta_and_plugin_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "TELEPILOT_VERSION", "0.106.0-beta.3")
    ok, reason = loader._manifest_compatible(SimpleNamespace(min_telepilot_version="0.106.0-beta.12", requires_features=[]))
    assert ok is False and "0.106.0-beta.12" in str(reason)
    ok, _ = loader._manifest_compatible(SimpleNamespace(min_telepilot_version="0.106.0", requires_features=[]))
    assert ok is True

    monkeypatch.setattr(loader, "TELEPILOT_VERSION", "0.106.0-beta.12")
    ok, _ = loader._manifest_compatible(SimpleNamespace(min_telepilot_version="0.106.0-beta.12", requires_features=[]))
    assert ok is True

    ok, reason = loader._manifest_compatible(SimpleNamespace(plugin_api=">=2", requires_features=[]))
    assert ok is False and "Plugin API" in str(reason)
    ok, reason = loader._manifest_compatible(SimpleNamespace(plugin_api="??", requires_features=[]))
    assert ok is False and "plugin_api" in str(reason)
    ok, _ = loader._manifest_compatible(SimpleNamespace(plugin_api="^1.0", requires_features=[]))
    assert ok is True


def test_preflight_reads_plugin_api_from_plugin_json(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo_api"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "demo_api", "version": "1.0.0", "plugin_api": ">=9"}), encoding="utf-8"
    )
    ok, reason = loader._preflight_installed_plugin(plugin_dir)
    assert ok is False and "Plugin API" in str(reason)

    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "demo_api", "version": "1.0.0", "plugin_api": "^1.0"}), encoding="utf-8"
    )
    assert loader._preflight_installed_plugin(plugin_dir) == (True, None)

    manifest = SimpleNamespace(key="demo_api", plugin_api=None, min_telebot_version=None, min_telepilot_version=None)
    loader._merge_static_plugin_api(plugin_dir, manifest)
    assert manifest.plugin_api == "^1.0"


def test_plugin_json_schema_accepts_plugin_api() -> None:
    from app.services.remote_plugin_service import PluginMetadataSchema

    assert PluginMetadataSchema(name="demo", plugin_api="^1.0").plugin_api == "^1.0"
    assert PluginMetadataSchema(name="demo").plugin_api is None
    with pytest.raises(ValueError):
        PluginMetadataSchema(name="demo", plugin_api="one.two")


# ─────────────────────────────────────────────────────
# 钩子统计
# ─────────────────────────────────────────────────────
def _state(redis: Any = None) -> loader._AccountState:
    state = loader._AccountState(7)
    state.redis = redis
    return state


async def test_invoke_records_ok_fail_timeout_and_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "plugin_update_in_progress", lambda *args, **kwargs: False)
    monkeypatch.setattr(loader.app_settings, "plugin_circuit_failure_threshold", 2)
    state = _state()

    async def ok() -> list[dict]:
        return []

    async def boom() -> None:
        raise ValueError("token=abcdefghijklmnop secret")

    assert await loader._invoke_plugin_with_resilience(state, "demo", ok, hook="on_event", event_type="message") == []
    with pytest.raises(ValueError):
        await loader._invoke_plugin_with_resilience(state, "demo", boom, hook="on_event", event_type="message")
    with pytest.raises(ValueError):
        await loader._invoke_plugin_with_resilience(state, "demo", boom, hook="on_event", event_type="message")
    with pytest.raises(RuntimeError, match="PLUGIN_CIRCUIT_OPEN"):
        await loader._invoke_plugin_with_resilience(state, "demo", ok, hook="on_event", event_type="message")

    state.plugin_circuits.clear()
    monkeypatch.setattr(loader.app_settings, "plugin_invoke_timeout_seconds", 0.05)

    async def slow() -> None:
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        await loader._invoke_plugin_with_resilience(state, "demo", slow, hook="on_command", event_type=None)

    snapshot = plugin_stats.get_recorder().local_snapshot("demo")
    rows = {(row["hook"], row["event_type"]): row for row in snapshot["hooks"]}
    event_row = rows[("on_event", "message")]
    assert event_row["calls"] == 4
    assert event_row["ok"] == 1
    assert event_row["fail"] == 2
    assert event_row["circuit_open"] == 1
    assert event_row["account_id"] == 7
    assert "ValueError" in event_row["last_error"]
    assert rows[("on_command", "-")]["timeout"] == 1


async def test_stats_flush_and_parse_roundtrip() -> None:
    redis = _FakeRedis()
    recorder = plugin_stats.get_recorder()
    for duration in (5, 40, 90, 300):
        recorder.record(account_id=3, plugin_key="demo", hook="on_event", event_type="message", outcome="ok", duration_ms=duration)
    recorder.record(
        account_id=3,
        plugin_key="demo",
        hook="on_event",
        event_type="message",
        outcome="fail",
        duration_ms=1200,
        error=RuntimeError("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz"),
    )
    recorder.record(account_id=4, plugin_key="demo", hook="on_startup", outcome="ok", duration_ms=2)
    dep_mod.note_deprecated("ai.tag_alias", plugin_key="demo", account_id=3)
    dep_mod.note_deprecated("ai.tag_alias", plugin_key="demo", account_id=3)

    written = await recorder.flush(redis)
    assert written == 3
    assert redis.expires[plugin_stats.STATS_KEY_PREFIX + "demo"] == plugin_stats.STATS_TTL_SECONDS
    # 第二次 flush 只写增量
    recorder.record(account_id=3, plugin_key="demo", hook="on_event", event_type="message", outcome="timeout", duration_ms=30000)
    await recorder.flush(redis)

    data = await plugin_stats.read_plugin_stats(redis, "demo")
    rows = {(row["account_id"], row["hook"]): row for row in data["hooks"]}
    row = rows[(3, "on_event")]
    assert row["calls"] == 6
    assert row["ok"] == 4 and row["failed"] == 1 and row["timeout"] == 1
    assert row["max_ms"] == 30000
    assert row["p50_ms"] == 100
    assert row["p95_ms"] == 30000
    assert row["failure_rate"] == pytest.approx(2 / 6, abs=1e-3)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in (row["last_error"] or "")
    assert row["since"] is not None
    assert data["totals"]["calls"] == 7
    assert data["deprecations"] == [
        {"account_id": 3, "id": "ai.tag_alias", "count": 2, "last_at": data["deprecations"][0]["last_at"], "detail": None}
    ]

    only_account_4 = await plugin_stats.read_plugin_stats(redis, "demo", account_id=4)
    assert [item["hook"] for item in only_account_4["hooks"]] == ["on_startup"]

    assert await plugin_stats.reset_plugin_stats(redis, "demo") is True
    assert (await plugin_stats.read_plugin_stats(redis, "demo"))["hooks"] == []


async def test_flush_failure_requeues_pending() -> None:
    class _Broken(_FakeRedis):
        def pipeline(self, transaction: bool = False):  # noqa: ANN201
            raise ConnectionError("redis down")

    recorder = plugin_stats.get_recorder()
    recorder.record(account_id=1, plugin_key="demo", hook="on_event", outcome="ok", duration_ms=1)
    with pytest.raises(ConnectionError):
        await recorder.flush(_Broken())
    assert recorder.pending
    redis = _FakeRedis()
    await recorder.flush(redis)
    assert not recorder.pending
    assert (await plugin_stats.read_plugin_stats(redis, "demo"))["totals"]["calls"] == 1


async def test_delayed_flush_is_scheduled_with_redis_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_stats, "FLUSH_DELAY_SECONDS", 0.01)
    redis = _FakeRedis()
    recorder = plugin_stats.get_recorder()
    recorder.set_redis_factory(lambda: redis)
    plugin_stats.record_hook(account_id=1, plugin_key="demo", hook="on_event", outcome="ok", duration_ms=3)
    await asyncio.sleep(0.05)
    assert (await plugin_stats.read_plugin_stats(redis, "demo"))["totals"]["calls"] == 1


# ─────────────────────────────────────────────────────
# 弃用登记
# ─────────────────────────────────────────────────────
def test_note_deprecated_counts_every_call_and_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="app.worker.plugins.deprecations"):
        assert dep_mod.note_deprecated("hook.on_interaction", plugin_key="legacy", account_id=1, now=100.0) is True
        assert dep_mod.note_deprecated("hook.on_interaction", plugin_key="legacy", account_id=1, now=200.0) is False
        assert dep_mod.note_deprecated("not.registered", plugin_key="legacy") is False
    assert sum("hook.on_interaction" in record.getMessage() for record in caplog.records) == 1
    deps = plugin_stats.get_recorder().local_snapshot("legacy")["deprecations"]
    assert deps == [{"account_id": 1, "plugin_key": "legacy", "id": "hook.on_interaction", "count": 2}]


async def test_ai_tag_alias_is_registered_as_deprecated() -> None:
    from app.worker.plugins.ai_facade import PluginAI

    async def _no_providers(*args: Any, **kwargs: Any) -> list:
        return []

    ai = PluginAI(account_id=5, plugin_key="tagger", provider_loader=_no_providers)
    with pytest.warns(DeprecationWarning):
        with pytest.raises(Exception):  # noqa: B017 - 没有 Provider，只关心弃用登记
            await ai.complete("sys", "hi", tag="x")
    deps = plugin_stats.get_recorder().local_snapshot("tagger")["deprecations"]
    assert deps and deps[0]["id"] == "ai.tag_alias" and deps[0]["account_id"] == 5


# ─────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────
async def test_capabilities_and_stats_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import __version__
    from app.api import plugins as plugins_api

    caps = await plugins_api.get_plugin_capabilities(_user=None)
    assert caps.host_version == __version__
    assert caps.plugin_api_version == PLUGIN_API_VERSION
    names = {item.name for item in caps.capabilities}
    assert {"messages.guest_reply_markup", "runtime.hook_stats"} <= names
    assert {item.id for item in caps.deprecations} >= {"hook.on_interaction", "manifest.min_telebot_version"}

    redis = _FakeRedis()
    monkeypatch.setattr(plugins_api, "get_redis", lambda: redis)
    plugin_stats.record_hook(account_id=2, plugin_key="demo", hook="on_config_action", event_type="sync", outcome="ok", duration_ms=12)
    dep_mod.note_deprecated("manifest.min_telebot_version", plugin_key="demo", account_id=2)
    stats = await plugins_api.get_plugin_hook_stats("demo", _user=None)
    assert stats.available is True
    assert stats.hooks[0].hook == "on_config_action" and stats.hooks[0].calls == 1
    assert stats.deprecations[0].replacement
    assert stats.retention_days == 7

    await plugins_api.reset_plugin_hook_stats("demo", _user=None)
    assert (await plugins_api.get_plugin_hook_stats("demo", _user=None)).hooks == []

    with pytest.raises(Exception) as excinfo:
        await plugins_api.get_plugin_hook_stats("../etc", _user=None)
    assert getattr(excinfo.value, "status_code", None) == 400


async def test_stats_endpoint_degrades_when_redis_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import plugins as plugins_api

    class _Down(_FakeRedis):
        async def hgetall(self, key: str) -> dict[str, str]:
            raise ConnectionError("down")

    monkeypatch.setattr(plugins_api, "get_redis", lambda: _Down())
    stats = await plugins_api.get_plugin_hook_stats("demo", _user=None)
    assert stats.available is False and stats.message


# ─────────────────────────────────────────────────────
# 清理：重复名单收敛为一份
# ─────────────────────────────────────────────────────
def test_non_core_plugin_key_list_has_single_source() -> None:
    from app import feature_registry
    from app.services import feature_service

    assert loader._NON_CORE_BUILTIN_COMPAT_KEYS is feature_registry.NON_CORE_OPTIONAL_PLUGIN_KEYS
    assert feature_registry._NON_CORE_BUILTIN_COMPAT_KEYS is feature_registry.NON_CORE_OPTIONAL_PLUGIN_KEYS
    assert feature_service._LEGACY_OPTIONAL_PLUGIN_KEYS is feature_registry.NON_CORE_OPTIONAL_PLUGIN_KEYS
