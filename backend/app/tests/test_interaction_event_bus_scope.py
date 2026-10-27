"""交互 Bot → Event Bus 直接订阅的会话范围边界。

回归：交互规则（促销 / 游戏等关键词规则）配置的群范围曾被并进 Event Bus 的
``allowed_chat_ids``，把直接订阅 Event Bus 的插件（例如 interaction-ai-chat）挡在
新群之外，@Bot / 回复 Bot 毫无反应。现在：

- 直接订阅只受账号「允许会话」（会话白名单，ignored_peer 表）约束，留空 = 全部；
- 交互规则自身（关键词 / 会话模块 / 支付）仍按各自配置的群生效；
- 被允许会话拦截时记录明确日志与 trace（账号、chat_id、原因）。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import account_bot_runtime, account_bot_service, ignored_peer_service, platform_capabilities

AID = 41
BOT_ID = 999
BOT_USERNAME = "demo_bot"
NEW_GROUP = -1003545897362
GAME_GROUP = -1001111111111
OTHER_GROUP = -1002222222222


@pytest.fixture(autouse=True)
def _platform_ready():
    """模拟主进程能力缓存已就绪（与 test_account_bot.py 同一前置）。"""

    platform_capabilities._reset_for_tests()
    platform_capabilities._CACHE_READY = True
    platform_capabilities._DESIRED["ledger"] = True
    platform_capabilities._RUNTIME["ledger"] = "ready"
    yield
    platform_capabilities._reset_for_tests()


@pytest.fixture(autouse=True)
def _clean_caches():
    def _clear() -> None:
        account_bot_runtime._ACCOUNT_ALLOWED_CHATS_CACHE.clear()
        account_bot_runtime._INTERACTION_ROUTING_INDEX_CACHE.pop(AID, None)
        account_bot_runtime._EVENT_BUS_SCOPE_BLOCK_LOGGED.clear()
        account_bot_runtime._EVENT_BUS_KNOWN_USERS_CACHE.pop(AID, None)
        account_bot_runtime._EVENT_BUS_SUDO_USERS_CACHE.pop(AID, None)

    _clear()
    yield
    _clear()


def _ai_subscription() -> Any:
    # 与 interaction-ai-chat plugin.json 的两条订阅一致。
    return [
        account_bot_runtime.normalize_event_subscription(
            {"events": ["message"], "source": ["interaction_bot"], "scope": "all_allowed_chats"},
            plugin_key="interaction-ai-chat",
        ),
        account_bot_runtime.normalize_event_subscription(
            {"events": ["guest_message"], "source": ["interaction_bot"], "scope": "guest_all"},
            plugin_key="interaction-ai-chat",
        ),
    ]


def _game_rule(*, chat_ids: list[int] | None = None, enabled: bool = True) -> dict[str, Any]:
    return {
        "id": "game",
        "name": "24 点",
        "enabled": enabled,
        "chat_ids": [GAME_GROUP] if chat_ids is None else chat_ids,
        "action": "module",
        "trigger_mode": "keyword",
        "module_key": "game24",
        "module_action": "start_game24",
        "module_start_keywords": ["开局"],
    }


def _cfg(rules: list[dict[str, Any]]) -> dict[str, Any]:
    # 经 normalize 后与线上一致：cfg.chat_ids 会被首条启用规则的群覆盖（Web 保存时写规则群并集）。
    return account_bot_service.normalize_transfer_notice_config(
        {"enabled": True, "interaction_bot_id": BOT_ID, "rules": rules}
    )


def _incoming(text: str, *, chat_id: int = NEW_GROUP, update_id: int = 1, **extra: Any) -> account_bot_runtime.Incoming:
    return account_bot_runtime.Incoming(
        account_id=AID,
        token="123:token",
        update_id=update_id,
        user_id=2001,
        chat_id=chat_id,
        chat_type="supergroup",
        message_id=10 + update_id,
        text=text,
        **extra,
    )


def _mention() -> account_bot_runtime.Incoming:
    return _incoming(f"@{BOT_USERNAME} 这个插件怎么配置？", update_id=1)


def _keyword() -> account_bot_runtime.Incoming:
    return _incoming("开局", update_id=2)


def _reply_to_bot() -> account_bot_runtime.Incoming:
    return _incoming("那怎么改？", update_id=3, reply_to_user_id=BOT_ID, reply_to_message_id=5)


def _index(cfg: dict[str, Any], allowed: set[int] | None) -> account_bot_runtime.InteractionRoutingIndex:
    return account_bot_runtime._build_interaction_routing_index(
        cfg,
        subscriptions=_ai_subscription(),
        active_session_chat_ids=set(),
        account_allowed_chat_ids=allowed,
    )


class _PeersDB:
    """只回答 ignored_peer 查询的假会话：execute 返回允许会话的 peer_id。"""

    def __init__(self, peer_ids: list[int]) -> None:
        self.peer_ids = list(peer_ids)
        self.queries = 0

    async def execute(self, _stmt: Any) -> Any:
        self.queries += 1
        rows = list(self.peer_ids)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    async def get(self, _model: Any, _aid: Any) -> Any:
        return None


async def _account_state(monkeypatch, peer_ids: list[int], cfg: dict[str, Any]) -> dict[str, Any]:
    monkeypatch.setattr(account_bot_runtime, "_event_bus_cached_known_user_ids", AsyncMock(return_value=([], set())))
    monkeypatch.setattr(account_bot_runtime, "_event_bus_active_session_participant_ids", AsyncMock(return_value=set()))
    return await account_bot_runtime._event_bus_account_state(_PeersDB(peer_ids), _mention(), cfg)


def _dispatch_matches(incoming: account_bot_runtime.Incoming, state: dict[str, Any]) -> bool:
    decisions = account_bot_runtime.dispatch_event(
        account_bot_runtime._incoming_trace_payload(incoming),
        _ai_subscription(),
        state,
    ).decisions
    return any(item.matched for item in decisions)


@pytest.mark.parametrize("make", [_mention, _keyword, _reply_to_bot], ids=["mention", "keyword", "reply"])
@pytest.mark.asyncio
async def test_allowed_group_without_any_rule_reaches_event_bus(monkeypatch, make) -> None:
    cfg = _cfg([])
    index = _index(cfg, {NEW_GROUP})
    incoming = make()

    routes = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_EVENT_BUS in routes

    state = await _account_state(monkeypatch, [NEW_GROUP], cfg)
    assert state["allowed_chat_ids"] == [NEW_GROUP]
    assert _dispatch_matches(incoming, state) is True


@pytest.mark.parametrize("make", [_mention, _keyword, _reply_to_bot], ids=["mention", "keyword", "reply"])
@pytest.mark.asyncio
async def test_empty_allowed_chats_means_all_even_when_game_rule_scoped(monkeypatch, make) -> None:
    # 旧 bug：游戏规则只配了 GAME_GROUP，Event Bus 的允许范围被收窄成 GAME_GROUP。
    cfg = _cfg([_game_rule()])
    assert cfg["chat_ids"] == [GAME_GROUP]
    index = _index(cfg, set())
    incoming = make()

    routes = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_EVENT_BUS in routes
    assert account_bot_runtime._interaction_event_bus_blocked_by_account_scope(
        incoming, index, event_bus_enabled=True
    ) is False

    state = await _account_state(monkeypatch, [], cfg)
    assert state["allowed_chat_ids"] == "*"
    assert _dispatch_matches(incoming, state) is True


@pytest.mark.asyncio
async def test_group_outside_allowed_chats_is_not_delivered_and_logs_reason(monkeypatch, caplog) -> None:
    cfg = _cfg([])
    index = _index(cfg, {OTHER_GROUP})
    incoming = _mention()

    routes = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_EVENT_BUS not in routes
    assert account_bot_runtime._interaction_event_bus_blocked_by_account_scope(incoming, index, event_bus_enabled=True)

    spans: list[dict[str, Any]] = []

    async def _span(_trace, stage, status, **kwargs):  # noqa: ANN001
        spans.append({"stage": stage, "status": status, **kwargs})

    monkeypatch.setattr(account_bot_runtime, "record_span", _span)
    caplog.set_level(logging.DEBUG, logger=account_bot_runtime.log.name)
    await account_bot_runtime._record_event_bus_account_scope_block(object(), incoming)
    await account_bot_runtime._record_event_bus_account_scope_block(object(), incoming)

    info = [r for r in caplog.records if r.levelno == logging.INFO and "允许会话" in r.getMessage()]
    assert len(info) == 1, "同一 (账号, chat_id) 的拦截日志按时间窗限频"
    message = info[0].getMessage()
    assert f"account={AID}" in message
    assert f"chat_id={NEW_GROUP}" in message
    assert "reason=chat_not_in_allowed_chats" in message
    assert [item["reason_code"] for item in spans] == ["chat_not_in_allowed_chats", "chat_not_in_allowed_chats"]

    state = await _account_state(monkeypatch, [OTHER_GROUP], cfg)
    assert state["allowed_chat_ids"] == [OTHER_GROUP]
    assert _dispatch_matches(incoming, state) is False


@pytest.mark.asyncio
async def test_handle_update_delivers_to_event_bus_in_allowed_group_with_scoped_game_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    cfg = _cfg([_game_rule()])
    account_bot_runtime._set_interaction_routing_index_cache(
        AID,
        cfg,
        subscriptions=_ai_subscription(),
        active_session_chat_ids=set(),
        account_allowed_chat_ids={NEW_GROUP, GAME_GROUP},
    )
    event_bus = AsyncMock(return_value=(True, True))
    rule_command = AsyncMock(return_value=False)
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(
        account_bot_runtime,
        "_event_framework_flags",
        AsyncMock(return_value={"trace_enabled": False, "event_bus_delivery_enabled": True, "inline_updates_enabled": True}),
    )
    monkeypatch.setattr(account_bot_service, "get_transfer_notice_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(account_bot_runtime, "_account_allowed_chat_ids", AsyncMock(return_value=frozenset({NEW_GROUP, GAME_GROUP})))
    monkeypatch.setattr(account_bot_runtime, "_incoming_is_userbot_command_text", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "_try_handle_transfer_command", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "_try_handle_transfer_notice", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "_try_handle_event_bus_subscriptions", event_bus)
    monkeypatch.setattr(account_bot_runtime, "_try_handle_interaction_rule_command_or_keyword", rule_command)
    monkeypatch.setattr(account_bot_runtime, "_try_handle_interaction_module_message", AsyncMock(return_value=False))

    await account_bot_runtime._handle_interaction_update(
        AID,
        "bbot-token",
        {
            "update_id": 8101,
            "message": {
                "message_id": 811,
                "text": f"@{BOT_USERNAME} 你好",
                "from": {"id": 2001, "first_name": "AAA"},
                "chat": {"id": NEW_GROUP, "type": "supergroup"},
            },
        },
    )

    event_bus.assert_awaited_once()
    rule_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_update_outside_allowed_chats_skips_event_bus_with_log(monkeypatch, caplog) -> None:
    class _DB:
        async def __aenter__(self):
            raise AssertionError("被允许会话拦截且无其它路由时不应打开 DB")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    cfg = _cfg([_game_rule()])
    account_bot_runtime._set_interaction_routing_index_cache(
        AID,
        cfg,
        subscriptions=_ai_subscription(),
        active_session_chat_ids=set(),
        account_allowed_chat_ids={GAME_GROUP},
    )
    event_bus = AsyncMock(return_value=(True, True))
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(
        account_bot_runtime,
        "_event_framework_flags",
        AsyncMock(return_value={"trace_enabled": False, "event_bus_delivery_enabled": True, "inline_updates_enabled": True}),
    )
    monkeypatch.setattr(account_bot_runtime, "_try_handle_event_bus_subscriptions", event_bus)
    caplog.set_level(logging.INFO, logger=account_bot_runtime.log.name)

    await account_bot_runtime._handle_interaction_update(
        AID,
        "bbot-token",
        {
            "update_id": 8102,
            "message": {
                "message_id": 812,
                "text": f"@{BOT_USERNAME} 你好",
                "from": {"id": 2001, "first_name": "AAA"},
                "chat": {"id": NEW_GROUP, "type": "supergroup"},
            },
        },
    )

    event_bus.assert_not_awaited()
    assert any(
        "reason=chat_not_in_allowed_chats" in r.getMessage() and f"chat_id={NEW_GROUP}" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.parametrize(
    "rules",
    [
        [_game_rule()],
        [_game_rule(chat_ids=[OTHER_GROUP])],
        [_game_rule(enabled=False)],
        [],
    ],
    ids=["game-scoped", "game-moved", "game-disabled", "game-deleted"],
)
@pytest.mark.asyncio
async def test_editing_or_deleting_game_rule_does_not_affect_ai_delivery(monkeypatch, rules) -> None:
    cfg = _cfg(rules)
    index = _index(cfg, {NEW_GROUP})
    routes = account_bot_runtime._classify_interaction_routes(_mention(), index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_EVENT_BUS in routes

    state = await _account_state(monkeypatch, [NEW_GROUP], cfg)
    assert state["allowed_chat_ids"] == [NEW_GROUP]
    assert _dispatch_matches(_mention(), state) is True


def test_game_rule_keyword_still_limited_to_its_own_groups() -> None:
    cfg = _cfg([_game_rule()])
    index = _index(cfg, set())

    in_game = account_bot_runtime._classify_interaction_routes(
        _incoming("开局", chat_id=GAME_GROUP), index, event_bus_enabled=True
    )
    elsewhere = account_bot_runtime._classify_interaction_routes(
        _incoming("开局", chat_id=NEW_GROUP), index, event_bus_enabled=True
    )

    assert account_bot_runtime._ROUTE_KEYWORD in in_game
    assert account_bot_runtime._ROUTE_KEYWORD not in elsewhere
    # 关键词规则不在 NEW_GROUP 生效，但答疑插件仍收到消息。
    assert account_bot_runtime._ROUTE_EVENT_BUS in elsewhere


def test_guest_message_behavior_unchanged_with_restricted_allowed_chats() -> None:
    cfg = _cfg([_game_rule()])
    index = _index(cfg, {GAME_GROUP})
    guest = _incoming(f"@{BOT_USERNAME} 你好", chat_id=NEW_GROUP, guest_query_id="gq-1", event_type="guest_message")

    routes = account_bot_runtime._classify_interaction_routes(guest, index, event_bus_enabled=True)

    assert account_bot_runtime._ROUTE_EVENT_BUS in routes
    assert account_bot_runtime._ROUTE_KEYWORD not in routes
    assert account_bot_runtime._interaction_event_bus_blocked_by_account_scope(
        guest, index, event_bus_enabled=True
    ) is False
    decisions = account_bot_runtime.dispatch_event(
        account_bot_runtime._incoming_trace_payload(guest),
        _ai_subscription(),
        {"allowed_chat_ids": [GAME_GROUP], "owner_user_ids": [], "known_user_ids": []},
    ).decisions
    matched = {item.scope for item in decisions if item.matched}
    assert matched == {"guest_all"}


@pytest.mark.asyncio
async def test_account_allowed_chats_cached_and_invalidated_on_reload(monkeypatch) -> None:
    db = _PeersDB([NEW_GROUP])
    assert await account_bot_runtime._account_allowed_chat_ids(db, AID) == frozenset({NEW_GROUP})
    db.peer_ids = [OTHER_GROUP]
    assert await account_bot_runtime._account_allowed_chat_ids(db, AID) == frozenset({NEW_GROUP})
    assert db.queries == 1

    class _Redis:
        async def publish(self, *_args):  # noqa: ANN002
            return 1

    monkeypatch.setattr(ignored_peer_service, "get_redis", lambda: _Redis())
    await ignored_peer_service.notify_reload(AID)

    assert await account_bot_runtime._account_allowed_chat_ids(db, AID) == frozenset({OTHER_GROUP})
    assert db.queries == 2


@pytest.mark.asyncio
async def test_unknown_allowed_chats_does_not_narrow_fast_path() -> None:
    # 慢路径未加载允许会话（None）时不在预过滤阶段收窄，交给 dispatch_event 精确判断。
    cfg = _cfg([_game_rule()])
    index = _index(cfg, None)
    routes = account_bot_runtime._classify_interaction_routes(_mention(), index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_EVENT_BUS in routes
