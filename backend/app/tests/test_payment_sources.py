"""B1 转账通知解析下沉：payment_sources 扩展点、双跑对照、golden 与开关。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import account_bot_runtime, payment_notice_legacy, payment_sources
from app.worker.plugins import plugin_stats
from app.worker.plugins.capabilities import CAPABILITY_INDEX, PluginCapabilities

GOLDEN = Path(__file__).with_name("data") / "payment_notice_golden.json"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _golden_cases() -> list[dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def _legacy_plugin_parser(text: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    """与官方插件 payment-source-cn-transfer 同构：直接复用宿主旧解析（golden 证明两者一致）。"""

    return payment_notice_legacy.parse_transfer_notice(text)


def _source(parser: Any, key: str = "payment-source-test") -> payment_sources.PaymentSource:
    return payment_sources.PaymentSource(key=key, parser=parser, version="1.0.0", source_ids=("test",))


# ─────────────────────────────────────────────────────
# golden：宿主旧解析器不变；官方插件（如果在旁边）结果 100% 一致
# ─────────────────────────────────────────────────────


def test_golden_host_legacy_parser_unchanged() -> None:
    cases = _golden_cases()
    assert len(cases) >= 400
    assert sum(1 for case in cases if case["expected"] is not None) >= 300
    mismatches = [
        case["text"][:80]
        for case in cases
        if payment_notice_legacy.parse_transfer_notice(case["text"]) != case["expected"]
    ]
    assert mismatches == []
    # 运行时入口仍是同一个解析器（旧调用方 / 测试继续可用）
    for case in cases[:50]:
        assert account_bot_runtime._parse_transfer_notice(case["text"]) == case["expected"]


def _plugin_repo() -> Path | None:
    for raw in (os.environ.get("TELEPILOT_PLUGINS_REPO"), str(REPO_ROOT.parent / "telebot-plugins")):
        if raw and (Path(raw) / "payment-source-cn-transfer" / "parser.py").is_file():
            return Path(raw)
    return None


def test_golden_official_plugin_matches_host_when_available() -> None:
    repo = _plugin_repo()
    if repo is None:
        pytest.skip("未找到插件库检出（TELEPILOT_PLUGINS_REPO），插件侧 golden 由插件库 CI 校验")
    spec = importlib.util.spec_from_file_location("_cn_transfer_parser", repo / "payment-source-cn-transfer" / "parser.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    for case in _golden_cases():
        result = module.parse_payment_notice(case["text"], {})
        normalized, _unit = payment_sources.normalize_result(result)
        assert normalized == case["expected"], case["text"]


# ─────────────────────────────────────────────────────
# 返回值规范化
# ─────────────────────────────────────────────────────


def test_normalize_result_accepts_aliases_and_keeps_unit_out_of_settlement() -> None:
    parsed, unit = payment_sources.normalize_result(
        {"payer": " A ", "receiver": "B", "amount": "100", "currency": "魔力值", "receiver_user_id": 9}
    )
    assert parsed == {"payer_name": "A", "receiver_name": "B", "amount": 100, "receiver_user_id": 9}
    assert unit == "魔力值"
    assert payment_sources.normalize_result(None) == (None, None)


@pytest.mark.parametrize(
    "raw",
    [
        "A 转出 100",
        {"receiver_name": "B", "amount": 1},
        {"payer_name": "", "receiver_name": "B", "amount": 1},
        {"payer_name": "A", "receiver_name": "B", "amount": -1},
        {"payer_name": "A", "receiver_name": "B", "amount": True},
        {"payer_name": "A", "receiver_name": "B", "amount": 1.5},
        {"payer_name": "A", "receiver_name": "B", "amount": 1, "payer_user_id": "x"},
        {"payer_name": "A" * 300, "receiver_name": "B", "amount": 1},
    ],
)
def test_normalize_result_rejects_invalid(raw: Any) -> None:
    with pytest.raises(payment_sources.PaymentResultError):
        payment_sources.normalize_result(raw)


def test_compare_reports_field_diffs() -> None:
    legacy = {"payer_name": "A", "receiver_name": "B", "amount": 100}
    assert payment_sources.compare(legacy, dict(legacy)) == []
    assert payment_sources.compare(None, None) == []
    assert payment_sources.compare(legacy, None) == ["宿主识别为转账通知，插件未识别"]
    assert payment_sources.compare(None, legacy) == ["宿主未识别，插件识别为转账通知"]
    diffs = payment_sources.compare(legacy, {**legacy, "amount": 10})
    assert diffs == ["amount: 宿主=100 插件=10"]


# ─────────────────────────────────────────────────────
# 双跑
# ─────────────────────────────────────────────────────

TEXT = "转账成功\n付款人：路人A\n收款人：我的TG名\n金额：100"
LEGACY = {"payer_name": "路人A", "receiver_name": "我的TG名", "amount": 100}


async def _dual(parser: Any, *, authoritative: bool = False, texts: list[tuple[str, str]] | None = None, **kw: Any):
    return await payment_sources.dual_parse(
        texts or [("text", TEXT)],
        {"account_id": 7, "chat_id": -100, "message_id": 1, "sender_id": 42},
        payment_notice_legacy.parse_transfer_notice,
        authoritative=authoritative,
        sources=[_source(parser)] if parser is not None else [],
        **kw,
    )


@pytest.mark.asyncio
async def test_dual_parse_without_source_is_legacy_only() -> None:
    outcome = await _dual(None)
    assert outcome.verdict == payment_sources.VERDICT_NO_SOURCE
    assert outcome.result == LEGACY
    assert outcome.calls == []


@pytest.mark.asyncio
async def test_dual_parse_agree_records_stats() -> None:
    plugin_stats.get_recorder().reset_local()
    outcome = await _dual(_legacy_plugin_parser)
    assert outcome.verdict == payment_sources.VERDICT_AGREE
    assert outcome.result == LEGACY and outcome.used_plugin is False
    rows = plugin_stats.get_recorder().local_snapshot("payment-source-test")["hooks"]
    assert [(row["hook"], row["event_type"], row["calls"], row["ok"]) for row in rows] == [
        ("parse_payment_notice", "agree", 1, 1)
    ]


@pytest.mark.asyncio
async def test_dual_parse_mismatch_keeps_host_result_by_default() -> None:
    plugin_stats.get_recorder().reset_local()

    def wrong(text: str, meta: dict[str, Any]) -> dict[str, Any]:
        return {**LEGACY, "amount": 10, "unit": "蝌蚪"}

    outcome = await _dual(wrong)
    assert outcome.verdict == payment_sources.VERDICT_MISMATCH
    assert outcome.result == LEGACY
    assert outcome.plugin == {**LEGACY, "amount": 10}
    assert outcome.plugin_unit == "蝌蚪"
    assert outcome.diffs == ["amount: 宿主=100 插件=10"]
    rows = plugin_stats.get_recorder().local_snapshot("payment-source-test")["hooks"]
    assert rows[0]["event_type"] == "mismatch"


@pytest.mark.asyncio
async def test_dual_parse_authoritative_uses_plugin_result() -> None:
    def better(text: str, meta: dict[str, Any]) -> dict[str, Any]:
        return {"payer_name": "甲", "receiver_name": "乙", "amount": 5, "unit": "魔力值"}

    outcome = await _dual(better, authoritative=True, texts=[("text", "甲 转出 5 魔力值（新格式）")])
    assert outcome.legacy is None
    assert outcome.verdict == payment_sources.VERDICT_MISMATCH
    assert outcome.used_plugin is True
    # unit 不进入结算数据
    assert outcome.result == {"payer_name": "甲", "receiver_name": "乙", "amount": 5}

    def says_no(text: str, meta: dict[str, Any]) -> None:
        return None

    outcome = await _dual(says_no, authoritative=True)
    assert outcome.used_plugin is True and outcome.result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("authoritative", [False, True])
async def test_dual_parse_fail_open_on_error_invalid_and_timeout(authoritative: bool) -> None:
    def boom(text: str, meta: dict[str, Any]) -> Any:
        raise RuntimeError("插件炸了")

    def invalid(text: str, meta: dict[str, Any]) -> Any:
        return {"payer_name": "A"}

    async def slow_async(text: str, meta: dict[str, Any]) -> Any:
        await asyncio.sleep(1)
        return LEGACY

    def slow_sync(text: str, meta: dict[str, Any]) -> Any:
        time.sleep(0.3)
        return LEGACY

    for parser, outcome_name in (
        (boom, plugin_stats.OUTCOME_FAIL),
        (invalid, plugin_stats.OUTCOME_FAIL),
        (slow_async, plugin_stats.OUTCOME_TIMEOUT),
        (slow_sync, plugin_stats.OUTCOME_TIMEOUT),
    ):
        started = time.monotonic()
        outcome = await _dual(parser, authoritative=authoritative, timeout=0.05)
        assert time.monotonic() - started < 0.25
        assert outcome.verdict == payment_sources.VERDICT_PLUGIN_FAILED
        assert outcome.result == LEGACY
        assert outcome.used_plugin is False
        assert outcome.calls[0].outcome == outcome_name


@pytest.mark.asyncio
async def test_dual_parse_passes_meta_and_text_order_like_host() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def parser(text: str, meta: dict[str, Any]) -> dict[str, Any] | None:
        seen.append((text, dict(meta)))
        return payment_notice_legacy.parse_transfer_notice(text)

    outcome = await _dual(parser, texts=[("text", "转账成功"), ("reply_to_text", TEXT)])
    assert outcome.verdict == payment_sources.VERDICT_AGREE and outcome.result == LEGACY
    assert [item[1]["source"] for item in seen] == ["text", "reply_to_text"]
    assert seen[0][1] == {"account_id": 7, "chat_id": -100, "message_id": 1, "sender_id": 42, "source": "text"}


@pytest.mark.asyncio
async def test_dual_parse_first_recognizing_source_wins() -> None:
    def blind(text: str, meta: dict[str, Any]) -> None:
        return None

    outcome = await payment_sources.dual_parse(
        [("text", TEXT)],
        {"account_id": 1},
        payment_notice_legacy.parse_transfer_notice,
        sources=[_source(blind, "a-blind"), _source(_legacy_plugin_parser, "b-cn")],
    )
    assert outcome.plugin_key == "b-cn" and outcome.verdict == payment_sources.VERDICT_AGREE


@pytest.mark.asyncio
async def test_golden_dual_run_all_agree() -> None:
    plugin_stats.get_recorder().reset_local()
    for case in _golden_cases():
        outcome = await _dual(_legacy_plugin_parser, texts=[("text", case["text"])] if case["text"].strip() else [])
        assert outcome.verdict in {payment_sources.VERDICT_AGREE}, case["text"]


# ─────────────────────────────────────────────────────
# 发现：只加载声明了 payment_sources 的已启用插件
# ─────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self.rows)


def _row(tmp_path: Path, key: str, plugin_json: dict[str, Any] | None, **kw: Any) -> SimpleNamespace:
    path = tmp_path / key
    path.mkdir()
    if plugin_json is not None:
        (path / "plugin.json").write_text(json.dumps(plugin_json), encoding="utf-8")
    values = {
        "key": key,
        "installed_path": str(path),
        "manifest_json": {},
        "enabled": True,
        "trust_tier": "official",
        "signature_ok": True,
        "last_install_error": None,
        "version": "1.0.0",
        "updated_at": "t1",
    }
    values.update(kw)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_load_payment_sources_only_declared_and_allowed(monkeypatch, tmp_path: Path) -> None:
    rows = [
        _row(tmp_path, "payment-source-cn-transfer", {"payment_sources": [{"id": "cn_transfer"}]}),
        _row(tmp_path, "old_plugin", {"name": "old_plugin"}),
        _row(tmp_path, "unsigned", {"payment_sources": [{"id": "x"}]}, signature_ok=False),
        _row(tmp_path, "broken", {"payment_sources": [{"id": "y"}]}, last_install_error="boom"),
        _row(tmp_path, "no_export", {"payment_sources": ["z"]}),
    ]
    monkeypatch.setattr("app.db.base.AsyncSessionLocal", lambda: _FakeSession(rows))
    loaded: list[str] = []

    def fake_find(key: str) -> Any:
        loaded.append(key)
        return None if key == "no_export" else _legacy_plugin_parser

    monkeypatch.setattr(payment_sources, "_find_parser", fake_find)
    payment_sources.reset_cache()
    sources = await payment_sources.load_payment_sources()
    assert [item.key for item in sources] == ["payment-source-cn-transfer"]
    assert sources[0].source_ids == ("cn_transfer",)
    # 旧插件（没声明 payment_sources）根本不会被加载
    assert sorted(loaded) == ["no_export", "payment-source-cn-transfer"]
    # 30 秒缓存内不再查库 / 加载
    await payment_sources.load_payment_sources()
    assert sorted(loaded) == ["no_export", "payment-source-cn-transfer"]
    # 版本没变时强制刷新也复用已加载的解析函数
    await payment_sources.load_payment_sources(force=True)
    assert loaded.count("payment-source-cn-transfer") == 1


@pytest.mark.asyncio
async def test_load_payment_sources_db_failure_keeps_legacy(monkeypatch) -> None:
    def broken_session() -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.base.AsyncSessionLocal", broken_session)
    payment_sources.reset_cache()
    assert await payment_sources.load_payment_sources() == []


def test_route_hint_only_when_authoritative_and_sources() -> None:
    assert payment_sources.plugin_route_hint() is False
    payment_sources.set_sources_for_test([_source(_legacy_plugin_parser)])
    assert payment_sources.plugin_route_hint() is False
    payment_sources.set_authoritative_hint(True)
    assert payment_sources.plugin_route_hint() is True
    payment_sources.set_sources_for_test([])
    assert payment_sources.plugin_route_hint() is False


# ─────────────────────────────────────────────────────
# 运行时接入：可信 Bot 之后、Trace 告警、开关
# ─────────────────────────────────────────────────────


def _incoming(text: str, *, reply_to_text: str | None = None) -> Any:
    return account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=10,
        user_id=456,
        chat_id=-100123,
        message_id=70,
        text=text,
        display_name="TransferBot",
        reply_to_text=reply_to_text,
    )


@pytest.mark.asyncio
async def test_runtime_mismatch_records_trace_and_keeps_host(monkeypatch) -> None:
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value={}))
    spans: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_span(*args: Any, **kwargs: Any) -> None:
        spans.append((args, kwargs))

    monkeypatch.setattr(account_bot_runtime, "record_span", fake_span)

    def wrong(text: str, meta: dict[str, Any]) -> dict[str, Any]:
        return {**LEGACY, "receiver_name": "别人"}

    payment_sources.set_sources_for_test([_source(wrong)])
    parsed = await account_bot_runtime._parse_incoming_transfer_notice_with_sources(_incoming(TEXT), trace="evt_x")
    assert parsed == LEGACY
    (args, kwargs), = spans
    assert args[:3] == ("evt_x", "payment_source", account_bot_runtime.TRACE_STATUS_WARNING)
    assert kwargs["reason_code"] == "payment_source_mismatch"
    assert kwargs["used"] == "legacy" and kwargs["plugin_key"] == "payment-source-test"
    assert kwargs["reason_code"] in account_bot_runtime.EVENT_REASON_CODES


@pytest.mark.asyncio
async def test_runtime_authoritative_switch(monkeypatch) -> None:
    monkeypatch.setattr(
        account_bot_runtime,
        "_event_framework_flags",
        AsyncMock(return_value={"payment_source_plugin_authoritative": True}),
    )
    monkeypatch.setattr(account_bot_runtime, "record_span", AsyncMock())

    def better(text: str, meta: dict[str, Any]) -> dict[str, Any] | None:
        return {"payer_name": "甲", "receiver_name": "乙", "amount": 5} if "新格式" in text else None

    payment_sources.set_sources_for_test([_source(better)])
    assert await account_bot_runtime._parse_incoming_transfer_notice_with_sources(_incoming("甲给乙 5 新格式")) == {
        "payer_name": "甲",
        "receiver_name": "乙",
        "amount": 5,
    }
    # 插件出错 → 回退宿主旧解析
    payment_sources.set_sources_for_test([_source(lambda text, meta: 1 / 0)])
    assert await account_bot_runtime._parse_incoming_transfer_notice_with_sources(_incoming(TEXT)) == LEGACY


@pytest.mark.asyncio
async def test_runtime_dual_parse_crash_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value={}))
    monkeypatch.setattr(payment_sources, "dual_parse", AsyncMock(side_effect=RuntimeError("bug")))
    assert await account_bot_runtime._parse_incoming_transfer_notice_with_sources(_incoming(TEXT)) == LEGACY


@pytest.mark.asyncio
async def test_untrusted_sender_never_reaches_plugin(monkeypatch) -> None:
    called: list[str] = []

    def spy(text: str, meta: dict[str, Any]) -> None:
        called.append(text)
        return None

    payment_sources.set_sources_for_test([_source(spy)])
    cfg = {"enabled": True, "trusted_bot_ids": [999], "rules": []}
    handled = await account_bot_runtime._try_handle_transfer_notice(object(), _incoming(TEXT), cfg)
    assert handled is False
    assert called == []


@pytest.mark.asyncio
async def test_trusted_sender_calls_plugin_once(monkeypatch) -> None:
    called: list[dict[str, Any]] = []

    def spy(text: str, meta: dict[str, Any]) -> dict[str, Any] | None:
        called.append(meta)
        return payment_notice_legacy.parse_transfer_notice(text)

    payment_sources.set_sources_for_test([_source(spy)])
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value={}))
    monkeypatch.setattr(account_bot_runtime, "_resolve_transfer_notice_reply_chain", AsyncMock(return_value=None))
    monkeypatch.setattr(account_bot_runtime, "_select_transfer_notice_rule", AsyncMock(return_value=None))
    cfg = {"enabled": True, "trusted_bot_ids": [456], "rules": [], "reply_chain_verification_enabled": False}
    await account_bot_runtime._try_handle_transfer_notice(object(), _incoming(TEXT), cfg)
    assert [meta["sender_id"] for meta in called] == [456]


def test_routing_hint_lets_authoritative_plugin_see_unknown_format() -> None:
    cfg = {"enabled": True, "trusted_bot_ids": [456], "rules": []}
    index = account_bot_runtime._build_interaction_routing_index(
        cfg, subscriptions=None, active_session_chat_ids=None, account_allowed_chat_ids=None
    )
    incoming = _incoming("甲给乙 5 新格式")
    before = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    payment_sources.set_sources_for_test([_source(_legacy_plugin_parser)])
    payment_sources.set_authoritative_hint(True)
    after = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_PAYMENT_NOTICE not in before
    assert account_bot_runtime._ROUTE_PAYMENT_NOTICE in after


# ─────────────────────────────────────────────────────
# 能力、plugin.json 声明、运行设置
# ─────────────────────────────────────────────────────


def test_capability_registered() -> None:
    item = CAPABILITY_INDEX["payment.sources"]
    assert item.facades == frozenset()
    assert PluginCapabilities().has("payment.sources")
    from app import __version__
    from app.worker.plugins.versioning import compare_versions

    # since 必须是已发布（≤ 当前）的宿主版本；合并顺延版本号时随 bump 一起改。
    assert compare_versions(item.since, __version__) <= 0


def test_plugin_metadata_validates_payment_sources() -> None:
    from app.services.remote_plugin_service import PluginMetadataSchema

    ok = PluginMetadataSchema(name="p", payment_sources=[{"id": "cn_transfer", "units": ["魔力值"]}])
    assert ok.payment_sources == [{"id": "cn_transfer", "units": ["魔力值"]}]
    for bad in ([{"id": ""}], [{"id": "a"}, {"id": "a"}], ["cn"], [{"id": "a", "units": [1]}]):
        with pytest.raises(ValueError):
            PluginMetadataSchema(name="p", payment_sources=bad)
    assert payment_sources.declared_source_ids([{"id": "a"}, "b", {"id": ""}, 3, "a"]) == ("a", "b")


@pytest.mark.asyncio
async def test_system_settings_payment_source_switch_roundtrip(monkeypatch) -> None:
    from app.api import rate_limit
    from app.tests.test_system_settings import _FakeSettingsDB

    db = _FakeSettingsDB({"log_retention": {"trace_enabled": True}})
    monkeypatch.setattr(rate_limit, "_audit", AsyncMock())
    monkeypatch.setattr(rate_limit, "_broadcast_reload", AsyncMock())
    monkeypatch.setattr("app.worker.supervisor.invalidate_log_retention_cache", lambda: None)
    result = await rate_limit.patch_system_settings(
        rate_limit._SettingsPatch(log_retention=rate_limit._LogRetentionPatch(trace_enabled=True)),
        db,  # type: ignore[arg-type]
        SimpleNamespace(id=1),
    )
    assert result["log_retention"]["payment_source_plugin_authoritative"] is False
    result = await rate_limit.patch_system_settings(
        rate_limit._SettingsPatch(
            log_retention=rate_limit._LogRetentionPatch(payment_source_plugin_authoritative=True)
        ),
        db,  # type: ignore[arg-type]
        SimpleNamespace(id=1),
    )
    assert db.rows["log_retention"].value["payment_source_plugin_authoritative"] is True
    assert result["log_retention"]["payment_source_plugin_authoritative"] is True


def test_host_stats_card_labels_mismatch_rows() -> None:
    from app.services import plugin_ui_cards

    data = {
        "hooks": [
            {"hook": "parse_payment_notice", "event_type": "mismatch", "calls": 2, "ok": 2, "account_id": 1},
            {"hook": "parse_payment_notice", "event_type": "agree", "calls": 5, "ok": 5, "account_id": 1},
        ],
        "totals": {"calls": 7},
    }
    out = plugin_ui_cards._format_hook_rows(data, {1: "主号"})
    rows = {row["event_type"]: row for row in out["hooks"]}
    assert rows["与宿主不一致"]["health"] == "对照不一致"
    assert rows["与宿主一致"]["health"] == "正常"
    assert rows["与宿主一致"]["hook"] == "转账通知解析"
