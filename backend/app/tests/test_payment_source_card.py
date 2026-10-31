"""第 22 轮：「转账通知以插件结果为准」开关就绪（开 / 关完整路径）+ 资金类插件只读卡片。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.action_event import ACTION_EVENT_STATUS_OK, ActionEvent
from app.db.models.feature import AccountFeature, Feature
from app.db.models.log import EventTrace
from app.db.models.plugin import InstalledPlugin
from app.db.models.system import SystemSetting
from app.services import (
    account_bot_runtime,
    payment_notice_legacy,
    payment_source_ledger,
    payment_sources,
    plugin_funds_cards,
    plugin_ui_cards,
)
from app.services.action_tap import derive_ledger_columns

TEXT = "转账成功\n付款人：路人A\n收款人：我的TG名\n金额：100"
LEGACY = payment_notice_legacy.parse_transfer_notice(TEXT)
NEW_FORMAT = "【到账提醒】甲 → 乙 · 5 魔力值"


class _MemRedis:
    """够这里用的 Redis 替身：hash + list + expire。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = str(int(bucket.get(field, "0")) + int(amount))
        return int(bucket[field])

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        bucket = self.hashes.get(key, {})
        return sum(1 for field in fields if bucket.pop(field, None) is not None)

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = int(seconds)
        return True

    async def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        self.lists[key] = self.lists.get(key, [])[start : end + 1]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])[start : end + 1]


@pytest.fixture
def mem_redis(monkeypatch: pytest.MonkeyPatch) -> _MemRedis:
    import app.redis_client as redis_client

    redis = _MemRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    return redis


@pytest.fixture(autouse=True)
def _reset_sources():
    payment_sources.reset_cache()
    payment_sources.set_authoritative_hint(False)
    yield
    payment_sources.reset_cache()
    payment_sources.set_authoritative_hint(False)


def _source(parser: Any, key: str = "payment-source-cn-transfer") -> payment_sources.PaymentSource:
    return payment_sources.PaymentSource(key=key, parser=parser, version="1.0.0", source_ids=("cn_transfer",))


def _new_format_parser(text: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    if "到账提醒" in text:
        return {"payer_name": "甲", "receiver_name": "乙", "amount": 5, "unit": "魔力值"}
    return payment_notice_legacy.parse_transfer_notice(text)


def _incoming(text: str) -> Any:
    return account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=10,
        user_id=456,
        chat_id=-100123,
        message_id=70,
        text=text,
        display_name="TransferBot",
    )


# ─────────────────────────────────────────────────────
# 5：开关打开 / 关闭时的完整路径
# ─────────────────────────────────────────────────────


async def _run_full_path(monkeypatch: pytest.MonkeyPatch, *, authoritative: bool, text: str) -> dict[str, Any]:
    """路由分类 → 可信 Bot 白名单 → 双跑 → 规则匹配（打桩）→ 记录；返回每一步看到的东西。"""

    flags = {"payment_source_plugin_authoritative": authoritative}
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value=flags))
    monkeypatch.setattr(account_bot_runtime, "record_span", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_resolve_transfer_notice_reply_chain", AsyncMock(return_value=None))
    monkeypatch.setattr(account_bot_runtime, "_enrich_transfer_notice_payer_from_reply_target", AsyncMock(side_effect=lambda inc, parsed: parsed))
    observed: list[Any] = []
    monkeypatch.setattr(payment_source_ledger, "observe", lambda outcome, meta: observed.append(outcome))
    selected: list[dict[str, Any]] = []

    async def _select(db: Any, incoming: Any, cfg: Any, parsed: dict[str, Any]) -> None:
        selected.append(dict(parsed))
        return None

    monkeypatch.setattr(account_bot_runtime, "_select_transfer_notice_rule", _select)
    payment_sources.set_sources_for_test([_source(_new_format_parser)])
    # 与交互 Bot 入口一致：每条更新先按当前配置刷新路由提示。
    await account_bot_runtime._refresh_payment_source_route_hint(flags)
    cfg = {"enabled": True, "trusted_bot_ids": [456], "rules": [], "reply_chain_verification_enabled": False}
    index = account_bot_runtime._build_interaction_routing_index(
        cfg, subscriptions=None, active_session_chat_ids=None, account_allowed_chat_ids=None
    )
    incoming = _incoming(text)
    routes = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    handled = None
    if account_bot_runtime._ROUTE_PAYMENT_NOTICE in routes:
        handled = await account_bot_runtime._try_handle_transfer_notice(object(), incoming, cfg)
    return {"routes": routes, "handled": handled, "selected": selected, "observed": observed}


@pytest.mark.asyncio
async def test_switch_on_routes_unknown_format_to_plugin_and_settles_with_plugin_result(monkeypatch) -> None:
    result = await _run_full_path(monkeypatch, authoritative=True, text=NEW_FORMAT)
    assert account_bot_runtime._ROUTE_PAYMENT_NOTICE in result["routes"]
    # 规则匹配拿到的是插件结果（旧正则认不出这条），之后的结算链路与旧版完全相同。
    assert result["selected"] == [{"payer_name": "甲", "receiver_name": "乙", "amount": 5}]
    assert result["handled"] is True
    (outcome,) = result["observed"]
    assert outcome.used_plugin is True and outcome.verdict == payment_sources.VERDICT_MISMATCH


@pytest.mark.asyncio
async def test_switch_off_keeps_legacy_routing_and_result(monkeypatch) -> None:
    result = await _run_full_path(monkeypatch, authoritative=False, text=NEW_FORMAT)
    # 关闭时：旧正则认不出的消息不进付款通知链路，插件不会被调用。
    assert account_bot_runtime._ROUTE_PAYMENT_NOTICE not in result["routes"]
    assert result["selected"] == [] and result["observed"] == []
    # 旧正则认得的消息照旧以旧解析为准，插件只做对照。
    legacy = await _run_full_path(monkeypatch, authoritative=False, text=TEXT)
    assert legacy["selected"] == [dict(LEGACY)]
    (outcome,) = legacy["observed"]
    assert outcome.used_plugin is False and outcome.verdict == payment_sources.VERDICT_AGREE


@pytest.mark.asyncio
async def test_switch_on_plugin_failure_falls_back_to_legacy(monkeypatch) -> None:
    flags = {"payment_source_plugin_authoritative": True}
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value=flags))
    monkeypatch.setattr(account_bot_runtime, "record_span", AsyncMock())
    monkeypatch.setattr(payment_source_ledger, "observe", lambda outcome, meta: None)
    payment_sources.set_sources_for_test([_source(lambda text, meta: 1 / 0)])
    assert await account_bot_runtime._parse_incoming_transfer_notice_with_sources(_incoming(TEXT)) == LEGACY


@pytest.mark.asyncio
async def test_switch_on_without_active_source_keeps_legacy_routing(monkeypatch) -> None:
    flags = {"payment_source_plugin_authoritative": True}
    monkeypatch.setattr(payment_sources, "load_payment_sources", AsyncMock(return_value=[]))
    payment_sources.set_sources_for_test([])
    await account_bot_runtime._refresh_payment_source_route_hint(flags)
    assert payment_sources.plugin_route_hint() is False


def test_switch_view_states() -> None:
    assert plugin_funds_cards.switch_view(False, True)["effective"] == "legacy"
    assert plugin_funds_cards.switch_view(True, True) == {
        "authoritative": True,
        "effective": "plugin",
        "label": "以插件为准（生效中）",
        "tone": "accent",
    }
    stuck = plugin_funds_cards.switch_view(True, False)
    assert stuck["effective"] == "legacy" and stuck["tone"] == "warning"


# ─────────────────────────────────────────────────────
# 6：识别记录（Redis，只读卡片的数据来源）
# ─────────────────────────────────────────────────────


def _outcome(**kwargs: Any) -> payment_sources.DualParseOutcome:
    base: dict[str, Any] = {"result": None, "legacy": None, "verdict": payment_sources.VERDICT_AGREE}
    base.update(kwargs)
    return payment_sources.DualParseOutcome(**base)


def test_build_observation_counts_recognized_and_verdicts() -> None:
    agree = _outcome(result=LEGACY, legacy=LEGACY, plugin=dict(LEGACY), plugin_key="ps", plugin_unit="魔力值")
    (item,) = payment_source_ledger.build_observation(agree, {"account_id": 1, "chat_id": -1}, now=1000)
    assert item["plugin_key"] == "ps"
    assert item["counters"] == {"agree": 1, "recognized": 1, "amount": 100}
    assert item["recent"]["payer_name"] == "路人A" and item["recent"]["unit"] == "魔力值"
    # 两边都“不是转账通知”的普通消息不计数（打开开关后可信 Bot 的普通消息也会走双跑）。
    assert payment_source_ledger.build_observation(_outcome(plugin=None, plugin_key="ps"), {}) == []
    # 插件没认出、旧解析认出：记不一致，记录里标注插件未识别。
    miss = _outcome(result=LEGACY, legacy=LEGACY, plugin=None, plugin_key="ps", verdict=payment_sources.VERDICT_MISMATCH)
    (item,) = payment_source_ledger.build_observation(miss, {})
    assert item["counters"] == {"mismatch": 1}
    assert item["recent"]["recognized_by_plugin"] is False and item["recent"]["amount"] == 100
    # 插件失败：只记失败次数。
    failed = _outcome(
        legacy=LEGACY,
        verdict=payment_sources.VERDICT_PLUGIN_FAILED,
        plugin_key="ps",
        calls=[payment_sources.SourceCall(key="ps", outcome="fail", error=RuntimeError("x"))],
    )
    assert payment_source_ledger.build_observation(failed, {}) == [
        {"plugin_key": "ps", "counters": {"plugin_failed": 1}, "recent": None}
    ]
    assert payment_source_ledger.build_observation(_outcome(verdict=payment_sources.VERDICT_NO_SOURCE), {}) == []


@pytest.mark.asyncio
async def test_ledger_today_week_split_by_server_timezone(mem_redis: _MemRedis) -> None:
    tz = "Asia/Shanghai"
    now = datetime(2026, 11, 1, 1, 30, tzinfo=UTC).timestamp()  # 上海 09:30

    def item(at: float) -> dict[str, Any]:
        return {"plugin_key": "ps", "counters": {"recognized": 1, "amount": 100, "agree": 1}, "recent": {"at": int(at), "amount": 100}}

    await payment_source_ledger.write_observation(mem_redis, [item(now)], now=now)
    # 上海昨天 23:10（UTC 15:10）：算近 7 日，不算今日
    yesterday = now - 10 * 3600 - 20 * 60
    await payment_source_ledger.write_observation(mem_redis, [item(yesterday)], now=yesterday)
    # 9 天前：都不算，读取时清掉
    old = now - 9 * 86400
    await payment_source_ledger.write_observation(mem_redis, [item(old)], now=old)
    data = await payment_source_ledger.read_summary(mem_redis, "ps", timezone_name=tz, now=now)
    assert data["today"]["recognized"] == 1 and data["today"]["amount"] == 100
    assert data["week"]["recognized"] == 2 and data["week"]["agree"] == 2
    assert len(data["recent"]) == 2
    assert mem_redis.expires[payment_source_ledger.KEY_PREFIX + "ps"] == payment_source_ledger.RETENTION_SECONDS
    stale = [f for f in mem_redis.hashes[payment_source_ledger.KEY_PREFIX + "ps"] if int(f.split("|")[0]) < now - 8 * 86400]
    assert stale == []


@pytest.mark.asyncio
async def test_recent_list_is_capped(mem_redis: _MemRedis) -> None:
    for index in range(payment_source_ledger.RECENT_LIMIT + 5):
        await payment_source_ledger.write_observation(
            mem_redis, [{"plugin_key": "ps", "counters": {}, "recent": {"at": 1000 + index}}], now=1000
        )
    assert len(mem_redis.lists[payment_source_ledger.KEY_PREFIX + "ps" + payment_source_ledger.RECENT_SUFFIX]) == payment_source_ledger.RECENT_LIMIT


@pytest.mark.asyncio
async def test_runtime_records_observation_without_changing_result(monkeypatch) -> None:
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value={}))
    monkeypatch.setattr(account_bot_runtime, "record_span", AsyncMock())
    seen: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(payment_source_ledger, "observe", lambda outcome, meta: seen.append((outcome, meta)))
    payment_sources.set_sources_for_test([_source(_new_format_parser)])
    assert await account_bot_runtime._parse_incoming_transfer_notice_with_sources(_incoming(TEXT)) == LEGACY
    ((outcome, meta),) = seen
    assert outcome.verdict == payment_sources.VERDICT_AGREE and meta["chat_id"] == -100123


# ─────────────────────────────────────────────────────
# 6：卡片（只读，只在插件详情）
# ─────────────────────────────────────────────────────


@pytest.fixture
async def card_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE account (id BIGINT PRIMARY KEY)"))
        await conn.execute(text("INSERT INTO account (id) VALUES (7)"))
        for table in (ActionEvent.__table__, EventTrace.__table__, Feature.__table__, AccountFeature.__table__, InstalledPlugin.__table__, SystemSetting.__table__):
            await conn.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Feature(key="payment-source-cn-transfer", display_name="中文转账通知解析", manifest={}))
        db.add(Feature(key="lucky_redpack", display_name="口令红包", manifest={}))
        db.add(Feature(key="weather", display_name="天气", manifest={}))
        db.add(
            InstalledPlugin(
                key="payment-source-cn-transfer",
                source="remote",
                version="1.0.0",
                enabled=True,
                signature_ok=True,
                manifest_json={"payment_sources": [{"id": "cn_transfer"}]},
                lint_warnings=[],
            )
        )
        db.add(
            InstalledPlugin(
                key="lucky_redpack",
                source="remote",
                version="1.4.14",
                enabled=True,
                signature_ok=True,
                manifest_json={"permissions": ["send_message", "payout"], "requires_platform_capabilities": ["ledger"]},
                lint_warnings=[],
            )
        )
        db.add(
            InstalledPlugin(key="weather", source="remote", version="1.0.0", enabled=True, signature_ok=True, manifest_json={}, lint_warnings=[])
        )
        db.add(SystemSetting(key="log_retention", value={"payment_source_plugin_authoritative": False}))
        db.add(SystemSetting(key="timezone", value={"value": "Asia/Shanghai"}))
        await db.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def _add_ledger_row(factory: Any, *, action_type: str, summary: dict[str, Any], created_at: datetime, plugin_key: str = "lucky_redpack") -> None:
    direction, amount, chat_id = derive_ledger_columns(action_type, summary)
    async with factory() as db:
        db.add(
            ActionEvent(
                account_id=7,
                channel="userbot",
                plugin_key=plugin_key,
                entry_key="main",
                action_type=action_type,
                params_summary=summary,
                status=ACTION_EVENT_STATUS_OK,
                direction=direction,
                amount=amount,
                chat_id=chat_id,
                created_at=created_at,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_host_cards_only_for_funds_plugins_and_only_in_plugin_detail(card_db, monkeypatch) -> None:
    monkeypatch.setattr(payment_sources, "load_payment_sources", AsyncMock(return_value=[_source(_new_format_parser)]))
    async with card_db() as db:
        payment, _ = await plugin_ui_cards.list_cards(db, slot="plugin.overview", plugin_key="payment-source-cn-transfer")
        funds, _ = await plugin_ui_cards.list_cards(db, slot="plugin.overview", plugin_key="lucky_redpack")
        plain, _ = await plugin_ui_cards.list_cards(db, slot="plugin.overview", plugin_key="weather")
        dashboard, _ = await plugin_ui_cards.list_cards(db, slot="dashboard")
    assert [card["card_id"] for card in payment] == ["_host.stats", "_host.payment_source"]
    assert [card["card_id"] for card in funds] == ["_host.stats", "_host.funds"]
    assert [card["card_id"] for card in plain] == ["_host.stats"]
    assert all(not card["card_id"].startswith("_host.") for card in dashboard)
    # 只读：卡片没有按钮块，任何按钮动作都被拒绝。
    for card in (plugin_funds_cards.payment_source_card(), plugin_funds_cards.funds_card()):
        assert all(block["type"] != "buttons" for block in card["blocks"])
    async with card_db() as db:
        with pytest.raises(plugin_ui_cards.PluginCardError):
            await plugin_ui_cards.card_action(db, plugin_key="lucky_redpack", card_id="_host.funds", action_id="reset_stats")
        # 不是资金类插件时读不到这张卡。
        missing = await plugin_ui_cards.card_data(db, plugin_key="weather", card_id="_host.funds")
    assert missing["status"] == "error"


@pytest.mark.asyncio
async def test_payment_source_card_data(card_db, mem_redis, monkeypatch) -> None:
    monkeypatch.setattr(payment_sources, "load_payment_sources", AsyncMock(return_value=[_source(_new_format_parser)]))
    now = datetime.now(UTC).timestamp()
    agree = _outcome(result=LEGACY, legacy=LEGACY, plugin=dict(LEGACY), plugin_key="payment-source-cn-transfer", plugin_unit="魔力值")
    mismatch = _outcome(
        result=LEGACY,
        legacy=LEGACY,
        plugin={**LEGACY, "amount": 99},
        plugin_key="payment-source-cn-transfer",
        verdict=payment_sources.VERDICT_MISMATCH,
    )
    for outcome in (agree, agree, mismatch):
        await payment_source_ledger.write_observation(mem_redis, payment_source_ledger.build_observation(outcome, {}, now=now), now=now)
    async with card_db() as db:
        result = await plugin_ui_cards.card_data(db, plugin_key="payment-source-cn-transfer", card_id="_host.payment_source")
    assert result["status"] == "ok", result
    data = result["data"]
    assert data["switch"]["effective"] == "legacy" and data["switch"]["label"].startswith("以旧解析为准")
    assert data["source"]["tone"] == "success" and "对照" in data["source"]["label"]
    assert data["today"]["recognized"] == 3 and data["today"]["amount"] == 299
    assert data["week"]["agree"] == 2 and data["week"]["mismatch"] == 1
    assert data["tones"] == {"agree": "success", "mismatch": "warning", "plugin_failed": "neutral"}
    assert data["recent"][0]["verdict_label"] == "不一致" and data["recent"][0]["used_label"] == "旧解析"
    assert data["recent"][1]["amount_text"] == "100 魔力值"


@pytest.mark.asyncio
async def test_funds_card_data_reads_ledger_readonly(card_db, mem_redis, monkeypatch) -> None:
    monkeypatch.setattr(payment_sources, "load_payment_sources", AsyncMock(return_value=[_source(_new_format_parser)]))
    now = datetime.now(UTC)
    await _add_ledger_row(
        card_db,
        action_type="payout",
        summary={"amount": 50, "chat_id": -100123, "chat_title": "测试群", "receiver_name": "小明"},
        created_at=now - timedelta(minutes=5),
    )
    await _add_ledger_row(
        card_db,
        action_type="payout",
        summary={"amount": 20, "chat_id": -100123, "receiver_name": "小红"},
        created_at=now - timedelta(days=3),
    )
    await _add_ledger_row(
        card_db,
        action_type="payout",
        summary={"amount": 999, "chat_id": -100123, "receiver_name": "旧账"},
        created_at=now - timedelta(days=10),
    )
    await _add_ledger_row(
        card_db,
        action_type="payout",
        summary={"amount": 7, "chat_id": -100123, "receiver_name": "别的插件"},
        created_at=now - timedelta(minutes=1),
        plugin_key="game24",
    )
    async with card_db() as db:
        before = (await db.execute(text("SELECT count(*) FROM action_event"))).scalar()
        result = await plugin_ui_cards.card_data(db, plugin_key="lucky_redpack", card_id="_host.funds")
        after = (await db.execute(text("SELECT count(*) FROM action_event"))).scalar()
    assert before == after == 4
    assert result["status"] == "ok", result["message"]
    data = result["data"]
    assert data["week"]["out_count"] == 2 and data["week"]["payout"] == 70
    assert data["today"]["out_count"] in (1, 2)  # 3 天前那笔不算今日；5 分钟前那笔视服务器时区可能跨日
    assert data["today"]["payout"] >= 50
    assert [row["counterpart"] for row in data["recent"]] == ["小明", "小红"]
    assert data["recent"][0]["direction_label"] == "发出" and data["recent"][0]["chat"] == "测试群"
    assert data["switch"]["effective"] == "legacy"
    assert data["verdicts"]["label"] == "一致 0 · 不一致 0 · 插件失败 0"


@pytest.mark.asyncio
async def test_status_endpoint_follows_current_config(card_db, monkeypatch) -> None:
    from app.api import plugin_ui as plugin_ui_api

    monkeypatch.setattr(payment_sources, "load_payment_sources", AsyncMock(return_value=[_source(_new_format_parser)]))
    async with card_db() as db:
        off = await plugin_ui_api.get_payment_source_status(db, SimpleNamespace(id=1))
        row = await db.get(SystemSetting, "log_retention")
        row.value = {"payment_source_plugin_authoritative": True}
        await db.commit()
        on = await plugin_ui_api.get_payment_source_status(db, SimpleNamespace(id=1))
    assert off.authoritative is False and off.effective == "legacy"
    assert on.authoritative is True and on.effective == "plugin" and on.label == "以插件为准（生效中）"
    assert on.setting_key == "log_retention.payment_source_plugin_authoritative"
    assert [item.plugin_key for item in on.sources] == ["payment-source-cn-transfer"]
    assert on.sources[0].active is True
    # 插件没加载成功：开关虽然打开，实际仍按旧解析，说明原因。
    monkeypatch.setattr(payment_sources, "load_payment_sources", AsyncMock(return_value=[]))
    async with card_db() as db:
        row = await db.get(SystemSetting, "log_retention")
        row.value = {"payment_source_plugin_authoritative": True}
        await db.commit()
        stuck = await plugin_ui_api.get_payment_source_status(db, SimpleNamespace(id=1))
    assert stuck.effective == "legacy" and stuck.sources[0].active is False
    assert "旧解析" in (stuck.sources[0].inactive_reason or "")


def test_recent_rows_are_json_safe() -> None:
    outcome = _outcome(result=LEGACY, legacy=LEGACY, plugin=dict(LEGACY), plugin_key="ps")
    (item,) = payment_source_ledger.build_observation(outcome, {"account_id": 1, "chat_id": -1}, now=1)
    assert json.loads(json.dumps(item["recent"], ensure_ascii=False))["receiver_name"] == "我的TG名"
