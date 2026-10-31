"""兼容层使用统计（第 22 轮）：只计数、不告警、不改变兼容层行为；SQLite / PostgreSQL 都能写。"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import compat_usage, plugin_compat
from app.db.models.plugin_compat_usage import PluginCompatUsage
from app.services import payment_sources, plugin_compat_usage
from app.tests._sqlite_engine import create_file_sqlite_engine
from app.tests.test_plugin_compat_layer import COMPAT_LAYER, PLAN_DOC
from app.worker.plugins import loader as loader_mod
from app.worker.plugins.deprecations import DEPRECATION_INDEX, note_deprecated, reset_warn_state


@pytest.fixture(autouse=True)
def _clean_counter():
    compat_usage.reset_for_test()
    plugin_compat_usage.disable_persistence()
    yield
    compat_usage.reset_for_test()
    plugin_compat_usage.disable_persistence()


@pytest.fixture(params=["sqlite", "postgresql"])
async def usage_db(request, tmp_path):
    schema = None
    if request.param == "postgresql":
        url = os.getenv("TELEPILOT_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("设置 TELEPILOT_TEST_POSTGRES_URL 运行真实 PostgreSQL")
        schema = f"compat_{uuid4().hex}"
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_file_sqlite_engine(tmp_path / "compat.sqlite")
    try:
        async with engine.begin() as connection:
            if schema:
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.run_sync(PluginCompatUsage.__table__.create)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()


def _pending() -> dict[tuple[str, str], int]:
    return {key: value[0] for key, value in compat_usage.pending_snapshot().items()}


# ─────────────────────────────────────────────────────
# 清单
# ─────────────────────────────────────────────────────
def test_catalog_matches_compat_layer_list_and_doc() -> None:
    assert [item.id for item in compat_usage.COMPAT_ITEMS] == list(COMPAT_LAYER)
    doc = PLAN_DOC.read_text(encoding="utf-8")
    assert "GET /api/plugins/compat-usage" in doc
    # 无法计数的只有 C01（类属性）与 C07（payload 字段）
    assert {item.id for item in compat_usage.COMPAT_ITEMS if not item.trackable} == {"C01", "C07"}
    assert all(item.counts for item in compat_usage.COMPAT_ITEMS)


def test_deprecation_mapping_points_at_registered_items() -> None:
    for dep_id, compat_id in compat_usage.DEPRECATION_TO_COMPAT.items():
        assert dep_id in DEPRECATION_INDEX
        assert compat_id in compat_usage.COMPAT_INDEX


# ─────────────────────────────────────────────────────
# 内存计数
# ─────────────────────────────────────────────────────
def test_hit_counts_per_item_and_plugin_without_logging(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    compat_usage.hit("C15", "old_plugin", now=100.0)
    compat_usage.hit("C15", "old_plugin", now=105.0)
    compat_usage.hit("C15", "other", now=101.0)
    compat_usage.hit("C20", None, now=102.0)
    compat_usage.hit("C99", "ignored")  # 未登记编号：忽略
    snap = compat_usage.pending_snapshot()
    assert snap[("C15", "old_plugin")] == (2, 100.0, 105.0)
    assert snap[("C15", "other")][0] == 1
    assert snap[("C20", compat_usage.HOST_PLUGIN_KEY)][0] == 1
    assert ("C99", "ignored") not in snap
    assert caplog.records == []  # 只计数，不写任何日志


def test_hit_never_raises_and_drain_requeue_roundtrip() -> None:
    compat_usage.hit("C12", object())  # 奇怪的 key 也不抛
    compat_usage.hit("C12", "x", now=10.0)
    rows = compat_usage.drain()
    assert compat_usage.pending_snapshot() == {}
    compat_usage.hit("C12", "x", now=20.0)
    compat_usage.requeue(rows)
    assert compat_usage.pending_snapshot()[("C12", "x")] == (2, 10.0, 20.0)


def test_note_deprecated_also_counts_compat_item() -> None:
    reset_warn_state()
    note_deprecated("ai.tag_alias", plugin_key="ai_plugin", account_id=1)
    note_deprecated("hook.on_interaction", plugin_key="game", account_id=1)
    assert _pending() == {("C04", "ai_plugin"): 1, ("C02", "game"): 1}


# ─────────────────────────────────────────────────────
# 各兼容层入口：命中计数、结果不变
# ─────────────────────────────────────────────────────
def test_plugin_compat_entries_count_only_when_compat_applies() -> None:
    # C08 兜底入口表：命中才计数，结果与之前一致
    assert plugin_compat.fallback_entry_session_scope("math10", "start_math10") == "chat"
    assert plugin_compat.fallback_entry_session_scope("new_plugin", "start") is None
    assert plugin_compat.fallback_entry_participant_policy("blackjack", "start_blackjack") == "solo_owner"
    assert plugin_compat.fallback_entry_has_field("pt_promote", "promote_torrent", "prize") is False
    assert plugin_compat.fallback_entry_keys("game24") == ["start_paid_game"]
    assert plugin_compat.fallback_entry_keys("new_plugin") == []
    # C10 模型名改写：只有真的改写才计数
    assert plugin_compat.normalize_legacy_feature_config("codex_image", {"model": "gpt-5.4"}) == {"model": "gpt-5.5"}
    plugin_compat.normalize_legacy_feature_config("codex_image", {"model": "gpt-5.5"})
    # C11 多 Token：新版本自带声明时不计数
    assert plugin_compat.legacy_secret_list_fields("chatgpt_image", {"properties": {}})
    declared = {"properties": {"tokens": {"x-secret-list": {"secret_field": "token"}}}}
    assert plugin_compat.legacy_secret_list_fields("chatgpt_image", declared) == {}
    # C12 旧试运行协议
    plugin_compat.legacy_dry_run_args("autorepeat", {}, {"message": "hi", "chat_id": 1})
    assert _pending() == {
        ("C08", "math10"): 1,
        ("C08", "blackjack"): 1,
        ("C08", "pt_promote"): 1,
        ("C08", "game24"): 1,
        ("C10", "codex_image"): 1,
        ("C11", "chatgpt_image"): 1,
        ("C12", "autorepeat"): 1,
    }


@pytest.mark.asyncio
async def test_payment_builtin_parser_counts_when_its_result_is_used() -> None:
    def legacy(text: str) -> dict[str, Any] | None:
        return {"amount": 5} if "转账" in text else None

    out = await payment_sources.dual_parse([("text", "转账 5")], {}, legacy, sources=[])
    assert out.result == {"amount": 5}
    await payment_sources.dual_parse([("text", "普通消息")], {}, legacy, sources=[])
    assert _pending() == {("C20", compat_usage.HOST_PLUGIN_KEY): 1}


@pytest.mark.asyncio
async def test_legacy_unsigned_plugin_load_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tests.test_plugin_loader import _FakeDB, _FakeInstalledPlugin

    monkeypatch.setattr(loader_mod.app_settings, "plugin_allow_legacy_unsigned_plugins", True)
    db = _FakeDB(
        accounts={},
        humanize={},
        afs=[],
        rules=[],
        installed_plugins={
            "legacy_unsigned": _FakeInstalledPlugin(
                key="legacy_unsigned", enabled=True, signature_ok=None, trust_tier="community"
            ),
            "signed": _FakeInstalledPlugin(key="signed", enabled=True, signature_ok=True, trust_tier="community"),
        },
    )
    assert (await loader_mod._authorize_installed_plugin(db, "legacy_unsigned")).allowed is True
    assert (await loader_mod._authorize_installed_plugin(db, "signed")).allowed is True
    assert _pending() == {("C19", "legacy_unsigned"): 1}


def test_direct_passthrough_legacy_return_values_unchanged() -> None:
    # C16 的计数在调用点，按返回值形状判断；这里只确认旧返回值语义不变
    coerce = loader_mod._coerce_direct_passthrough_outcome  # noqa: SLF001
    assert coerce(True) == ("consumed", None)
    assert coerce({"consume": True}) == ("consumed", None)
    assert coerce({"consume": False}) == ("ignored", None)


# ─────────────────────────────────────────────────────
# 持久化（SQLite / PostgreSQL）
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_flush_upserts_and_accumulates(usage_db) -> None:
    compat_usage.hit("C15", "old_plugin", now=100.0)
    compat_usage.hit("C15", "old_plugin", now=110.0)
    compat_usage.hit("C20", None, now=120.0)
    assert await plugin_compat_usage.flush(usage_db) == 2
    assert compat_usage.pending_snapshot() == {}
    # 另一个进程 / 下一轮写入：次数累加，first_at 取最早、last_at 取最晚
    compat_usage.hit("C15", "old_plugin", now=90.0)
    compat_usage.hit("C15", "old_plugin", now=200.0)
    assert await plugin_compat_usage.flush(usage_db) == 1
    assert await plugin_compat_usage.flush(usage_db) == 0

    async with usage_db() as db:
        items = await plugin_compat_usage.read_usage(db)
    by_id = {item["id"]: item for item in items}
    assert [item["id"] for item in items] == list(COMPAT_LAYER)
    c15 = by_id["C15"]
    assert c15["total"] == 4 and c15["plugin_count"] == 1
    assert c15["plugins"] == [{"plugin_key": "old_plugin", "count": 4, "first_at": 90.0, "last_at": 200.0}]
    assert by_id["C20"]["plugins"][0]["plugin_key"] == compat_usage.HOST_PLUGIN_KEY
    assert by_id["C01"]["trackable"] is False and by_id["C01"]["total"] == 0


@pytest.mark.asyncio
async def test_flush_failure_keeps_counts_in_memory() -> None:
    class _Broken:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *_exc):
            return False

    compat_usage.hit("C09", "math10", now=1.0)
    with pytest.raises(RuntimeError):
        await plugin_compat_usage.flush(lambda: _Broken())
    assert compat_usage.pending_snapshot()[("C09", "math10")][0] == 1
    assert await plugin_compat_usage.flush_quietly(lambda: _Broken()) == 0
    assert compat_usage.pending_snapshot()[("C09", "math10")][0] == 1


@pytest.mark.asyncio
async def test_enable_persistence_schedules_one_delayed_flush(usage_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_compat_usage, "FLUSH_DELAY_SECONDS", 0.01)
    plugin_compat_usage.enable_persistence(usage_db)
    compat_usage.hit("C14", "ai_chat")
    compat_usage.hit("C14", "ai_chat")
    task = plugin_compat_usage._state["task"]  # noqa: SLF001
    assert task is not None
    await task
    async with usage_db() as db:
        row = await db.get(PluginCompatUsage, ("C14", "ai_chat"))
    assert row is not None and row.count == 2


# ─────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_compat_usage_api_and_capabilities_flag(usage_db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.plugins import get_plugin_capabilities, get_plugin_compat_usage

    monkeypatch.setitem(plugin_compat_usage._state, "factory", usage_db)  # noqa: SLF001
    compat_usage.hit("C02", "guess_number", now=50.0)
    async with usage_db() as db:
        resp = await get_plugin_compat_usage(db, _user=None)  # type: ignore[arg-type]
    assert resp.available is True
    assert resp.flush_interval_seconds == int(plugin_compat_usage.FLUSH_DELAY_SECONDS)
    c02 = next(item for item in resp.items if item.id == "C02")
    assert c02.total == 1 and c02.plugins[0].plugin_key == "guess_number" and c02.last_at == 50.0

    caps = await get_plugin_capabilities(_user=None)  # type: ignore[arg-type]
    assert caps.compat_usage is True


@pytest.mark.asyncio
async def test_compat_usage_api_degrades_when_table_missing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.plugins import get_plugin_compat_usage

    engine = create_file_sqlite_engine(tmp_path / "empty.sqlite")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setitem(plugin_compat_usage._state, "factory", factory)  # noqa: SLF001
    try:
        async with factory() as db:
            resp = await get_plugin_compat_usage(db, _user=None)  # type: ignore[arg-type]
    finally:
        await engine.dispose()
    assert resp.available is False
    assert [item.id for item in resp.items] == list(COMPAT_LAYER)
