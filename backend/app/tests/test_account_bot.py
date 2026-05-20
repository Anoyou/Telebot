"""账号绑定 Bot 联动系统的关键安全单测。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models.account_bot import AccountBot
from app.db.models.log import RuntimeLog
from app.schemas.account_bot import AccountBotConfigUpdate
from app.services import account_bot_runtime, account_bot_service


class _MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, **kwargs):
        if kwargs.get("nx") and key in self.data:
            return False
        self.data[key] = value
        return True


def test_account_bot_config_response_hides_plain_token() -> None:
    """配置出参只暴露 has_token，不返回明文 token 或加密串。"""

    row = AccountBot(account_id=1, bot_token_enc="encrypted-placeholder")
    out = account_bot_service.config_to_response(row)

    assert out.has_token is True
    assert "token" not in out.model_dump()
    assert out.remote_plugin_policy.enabled is False
    assert out.remote_plugin_policy.install is False


def test_account_bot_role_matrix() -> None:
    """viewer/operator/admin 权限必须逐级包含。"""

    assert account_bot_service.role_allows("viewer", "viewer") is True
    assert account_bot_service.role_allows("viewer", "operator") is False
    assert account_bot_service.role_allows("operator", "viewer") is True
    assert account_bot_service.role_allows("operator", "admin") is False
    assert account_bot_service.role_allows("admin", "operator") is True


def test_account_bot_callback_data_parser() -> None:
    """callback data 必须绑定 aid/action/resource/nonce。"""

    assert account_bot_runtime._parse_callback("ab:12:feature_toggle:game24") == (
        12,
        "feature_toggle",
        "game24",
        None,
    )
    assert account_bot_runtime._parse_callback("ab:12:confirm:restart:n1") == (
        12,
        "confirm",
        "restart",
        "n1",
    )
    assert account_bot_runtime._parse_callback("bad:12:confirm:restart") is None


def test_account_bot_error_sanitizer_masks_token() -> None:
    token = "123456:secret-token"
    text = account_bot_service.sanitize_bot_error(
        f"https://api.telegram.org/bot{token}/sendMessage failed at /Users/me/project/file.py",
        token=token,
    )
    assert token not in text
    assert "api.telegram.org" not in text
    assert "/Users/me" not in text


def test_bot_polling_conflict_error_mentions_role() -> None:
    clean = "Conflict: terminated by other getUpdates request"

    management = account_bot_service.label_bot_polling_error(clean, role="management")
    interaction = account_bot_service.label_bot_polling_error(clean, role="interaction")

    assert "管理 Bot polling 冲突" in management
    assert "交互 Bot polling 冲突" in interaction
    assert "Bbot token" in interaction


def test_account_bot_token_payload_trims_whitespace() -> None:
    payload = AccountBotConfigUpdate(bot_token="  123456:secret-token  ")
    assert payload.bot_token == "123456:secret-token"


def test_account_bot_remote_plugin_policy_defaults_closed() -> None:
    policy = account_bot_service.normalize_remote_plugin_policy(None)
    assert policy == {
        "enabled": False,
        "install": False,
        "update": False,
        "uninstall": False,
        "enable_disable": False,
    }


def test_account_bot_remote_plugin_policy_update_is_partial() -> None:
    policy = account_bot_service.normalize_remote_plugin_policy(
        {"enabled": True, "install": True, "unknown": True}
    )
    assert policy["enabled"] is True
    assert policy["install"] is True
    assert policy["update"] is False
    assert "unknown" not in policy


def test_account_bot_transfer_notice_config_normalizes_values() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "chat_id": "-100123",
            "chat_ids": ["-100123", "-100999", "-100123"],
            "trusted_bot_id": "456",
            "trigger_text": " 转账成功 ",
            "trigger_texts": [" 转账成功 ", "交易成功", "转账成功"],
            "receiver_text": " 我 ",
            "amount": "100",
            "response_template": " 检测到 {amount} ",
        }
    )

    assert cfg["enabled"] is True
    assert cfg["chat_id"] == -100123
    assert cfg["chat_ids"] == [-100123, -100999]
    assert cfg["trusted_bot_id"] == 456
    assert cfg["trigger_text"] == "转账成功"
    assert cfg["trigger_texts"] == ["转账成功", "交易成功"]
    assert cfg["receiver_text"] == "我"
    assert cfg["amount"] == 100
    assert cfg["response_template"] == "检测到 {amount}"
    assert cfg["rules"][0]["id"] == "legacy-default"
    assert cfg["rules"][0]["chat_ids"] == [-100123, -100999]
    assert cfg["rules"][0]["trigger_texts"] == ["转账成功", "交易成功"]


def test_account_bot_interaction_rules_normalize_and_sync_first_rule() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "chat_id": "-100000",
            "trigger_text": "旧关键词",
            "action": "notice",
            "rules": [
                {
                    "id": "math",
                    "name": "算数题",
                    "enabled": True,
                    "chat_ids": ["-100123"],
                    "trigger_texts": ["交易成功"],
                    "amount": "88",
                    "action": "math10",
                    "math_prize": "456",
                },
                {
                    "id": "bad",
                    "enabled": True,
                    "trigger_texts": [],
                    "action": "unknown",
                },
            ],
        }
    )

    assert cfg["chat_id"] == -100123
    assert cfg["chat_ids"] == [-100123]
    assert cfg["trigger_text"] == "交易成功"
    assert cfg["amount"] == 88
    assert cfg["action"] == "math10"
    assert cfg["math_prize"] == 456
    assert cfg["rules"][1]["action"] == "notice"


def test_account_bot_interaction_rules_normalize_new_rule_fields() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "rules": [
                {
                    "id": "game24",
                    "enabled": True,
                    "chat_ids": ["-100123"],
                    "trigger_mode": "both",
                    "trigger_texts": ["转账成功"],
                    "module_start_keywords": ["开24点", "开24点"],
                    "amount": "100",
                    "amount_match_mode": "gte",
                    "action": "module",
                    "module_key": "game24",
                    "open_commands": ["开启24点"],
                    "close_commands": ["关闭24点"],
                    "status_commands": ["24点状态"],
                    "disabled_message": "今天休息",
                    "valid_seconds": "1200",
                    "concurrency": "chat",
                }
            ],
        }
    )

    rule = cfg["rules"][0]
    assert cfg["trigger_mode"] == "both"
    assert cfg["amount_match_mode"] == "gte"
    assert cfg["module_start_keywords"] == ["开24点"]
    assert cfg["valid_seconds"] == 1200
    assert rule["open_commands"] == ["开启24点"]
    assert rule["close_commands"] == ["关闭24点"]
    assert rule["status_commands"] == ["24点状态"]
    assert rule["disabled_message"] == "今天休息"


def test_disabled_or_cleared_interaction_bot_hides_stale_conflict_error() -> None:
    raw = {
        "enabled": False,
        "interaction_bot_token_enc": None,
        "interaction_last_error": "Conflict: terminated by other getUpdates request",
    }

    cfg = account_bot_service.normalize_transfer_notice_config(raw)

    assert cfg["has_interaction_bot_token"] is False
    assert cfg["interaction_runtime_status"] == "stopped"
    assert cfg["interaction_last_error"] is None


def test_account_bot_interaction_rule_normalizes_module_action() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "rules": [
                {
                    "id": "game24-ticket",
                    "enabled": True,
                    "chat_ids": ["-100123"],
                    "trigger_texts": ["转账成功"],
                    "amount": "100",
                    "action": "module",
                    "module_key": " game24 ",
                    "module_prize": "456",
                    "module_start_text": " 正在开启 24 点 ",
                }
            ],
        }
    )

    rule = cfg["rules"][0]
    assert cfg["action"] == "module"
    assert cfg["module_key"] == "game24"
    assert cfg["module_prize"] == 456
    assert rule["action"] == "module"
    assert rule["module_key"] == "game24"
    assert rule["module_prize"] == 456
    assert rule["module_start_text"] == "正在开启 24 点"


def test_account_bot_transfer_notice_parser() -> None:
    parsed = account_bot_runtime._parse_transfer_notice(
        "转账成功：\n付款人：路人A\n收款人：我的TG名\n金额：100"
    )

    assert parsed == {
        "payer_name": "路人A",
        "receiver_name": "我的TG名",
        "amount": 100,
    }
    compact = account_bot_runtime._parse_transfer_notice(
        "转账成功\nAAA 转出 100\n你心里已经有答案了 收到 100"
    )
    assert compact == {
        "payer_name": "AAA",
        "receiver_name": "你心里已经有答案了",
        "amount": 100,
    }
    assert account_bot_runtime._parse_transfer_notice("转账成功：金额：100") is None


def test_decrypt_bot_token_failure_is_user_fixable(monkeypatch) -> None:
    row = AccountBot(account_id=1, bot_token_enc="old-key-token")
    monkeypatch.setattr(
        account_bot_service,
        "decrypt_str",
        lambda _value: (_ for _ in ()).throw(ValueError("解密失败：可能 MASTER_KEY 已变更")),
    )

    with pytest.raises(account_bot_service.HTTPException) as exc_info:
        account_bot_service.decrypt_bot_token(row)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "ACCOUNT_BOT_TOKEN_DECRYPT_FAILED"


@pytest.mark.asyncio
async def test_transfer_notice_from_unauthed_abot_sends_group_notice(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

    send = AsyncMock()
    find_user = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", find_user)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "chat_id": -100123,
                "trusted_bot_id": 456,
                "trigger_text": "转账成功",
                "receiver_text": "我的TG名",
                "amount": 100,
                "response_template": "检测到 {payer_name} 向 {receiver_name} 转账 {amount}，已进入游戏流程。",
            }
        ),
    )
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": "转账成功：\n付款人：路人A\n收款人：我的TG名\n金额：100",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert find_user.await_count == 0
    assert send.await_count == 1
    assert send.await_args.args[:3] == (
        "bbot-token",
        -100123,
        "检测到 路人A 向 我的TG名 转账 100，已进入游戏流程。",
    )


@pytest.mark.asyncio
async def test_reply_plus_amount_sends_transfer_notice_with_abot_token(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

    send = AsyncMock(side_effect=[{"from": {"id": 456}, "message_id": 22}, {}])
    find_user = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", find_user)
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "chat_id": -100123,
                "trusted_bot_id": 456,
                "trigger_text": "转账成功",
                "receiver_text": None,
                "amount": None,
                "action": "notice",
                "math_prize": 123,
                "response_template": "检测到 {payer_name} 向 {receiver_name} 转账 {amount}，已进入游戏流程。",
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 2,
            "message": {
                "message_id": 20,
                "text": "+254",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 19,
                    "from": {"id": 222, "first_name": "BBB"},
                    "text": "hello",
                },
            },
        },
    )

    assert find_user.await_count == 0
    assert send.await_count == 2
    assert send.await_args_list[0].args[:3] == (
        "abot-token",
        -100123,
        "转账成功\nAAA 射出 254\nBBB 接收 254",
    )
    assert send.await_args_list[1].args[:3] == (
        "bbot-token",
        -100123,
        "检测到 AAA 向 BBB 转账 254，已进入游戏流程。",
    )


@pytest.mark.asyncio
async def test_plus_amount_falls_back_to_receiver_config_when_reply_missing(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

    send = AsyncMock(side_effect=[{"from": {"id": 456}, "message_id": 22}, {}])
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "chat_id": -100123,
                "trusted_bot_id": 456,
                "trigger_text": "转账成功",
                "receiver_text": "BBB",
                "amount": None,
                "action": "notice",
                "math_prize": 123,
                "response_template": "检测到 {payer_name} 向 {receiver_name} 转账 {amount}，已进入游戏流程。",
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 3,
            "message": {
                "message_id": 30,
                "text": "+100",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert send.await_args_list[0].args[:3] == (
        "abot-token",
        -100123,
        "转账成功\nAAA 射出 100\nBBB 接收 100",
    )


@pytest.mark.asyncio
async def test_reply_plus_amount_without_transfer_bot_token_waits_for_real_notice(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value=None))
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "chat_ids": [-100123],
                "trusted_bot_id": None,
                "trigger_texts": ["转账成功", "交易成功"],
                "receiver_text": None,
                "amount": None,
                "action": "notice",
                "math_prize": 123,
                "response_template": "检测到 {payer_name} 向 {receiver_name} 转账 {amount}，已进入游戏流程。",
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 4,
            "message": {
                "message_id": 40,
                "text": "+100",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 39,
                    "from": {"id": 222, "first_name": "BBB"},
                    "text": "hello",
                },
            },
        },
    )

    assert send.await_count == 0


@pytest.mark.asyncio
async def test_reply_plus_amount_respects_receiver_filter_before_module(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    send = AsyncMock()
    start_game24 = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(account_bot_runtime, "_start_game24_game", start_game24)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "game24-ticket",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "A",
                        "amount": 100,
                        "action": "module",
                        "module_key": "game24",
                        "module_prize": 123,
                        "response_template": "忽略",
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 8,
            "message": {
                "message_id": 80,
                "text": "+100",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 79,
                    "from": {"id": 222, "first_name": "B"},
                    "text": "hello",
                },
            },
        },
    )

    assert send.await_count == 0
    assert start_game24.await_count == 0


@pytest.mark.asyncio
async def test_transfer_notice_uses_first_matching_interaction_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    start_math = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", start_math)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "trigger_text": "转账成功",
                "trigger_texts": ["转账成功"],
                "rules": [
                    {
                        "id": "notice-100",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "action": "notice",
                        "math_prize": 123,
                        "response_template": "不应该命中",
                    },
                    {
                        "id": "math-200",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["交易成功", "转账成功"],
                        "receiver_text": "BBB",
                        "amount": 200,
                        "action": "math10",
                        "math_prize": 456,
                        "response_template": "忽略",
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 6,
            "message": {
                "message_id": 60,
                "text": "转账成功\nAAA 射出 200\nBBB 接收 200",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert start_math.await_count == 1
    assert start_math.await_args.kwargs == {"prize": 456}
    assert send.await_count == 0


@pytest.mark.asyncio
async def test_interaction_keyword_starts_module_and_payment_mode_ignores_notice(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    start_game24 = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_start_game24_game", start_game24)
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "keyword-game24",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "keyword",
                        "trigger_texts": ["转账成功"],
                        "module_start_keywords": ["开24点"],
                        "action": "module",
                        "module_key": "game24",
                        "module_prize": 321,
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 991,
            "message": {
                "message_id": 9910,
                "text": "转账成功\nAAA 射出 100\nBBB 接收 100",
                "from": {"id": 456, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )
    assert start_game24.await_count == 0

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 992,
            "message": {
                "message_id": 9920,
                "text": "开24点",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )
    assert start_game24.await_count == 1
    assert start_game24.await_args.kwargs["prize"] == 321


@pytest.mark.asyncio
async def test_interaction_closed_rule_only_replies_to_keyword(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    redis = _MemoryRedis()
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "keyword-game24",
                        "name": "24点",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "module_start_keywords": ["开24点"],
                        "close_commands": ["关闭24点"],
                        "disabled_message": "规则已关闭",
                        "receiver_text": "BBB",
                        "amount_match_mode": "gte",
                        "amount": 100,
                        "action": "notice",
                        "response_template": "不应该发送",
                    },
                ],
            }
        ),
    )

    for update_id, message_id, text, sender_id in [
        (993, 9930, "关闭24点", 111),
        (994, 9940, "转账成功\nAAA 射出 120\nBBB 接收 120", 456),
        (995, 9950, "开24点", 111),
    ]:
        await account_bot_runtime._handle_interaction_update(
            1,
            "bbot-token",
            {
                "update_id": update_id,
                "message": {
                    "message_id": message_id,
                    "text": text,
                    "from": {"id": sender_id, "first_name": "AAA"},
                    "chat": {"id": -100123, "type": "supergroup"},
                },
            },
        )

    sent_texts = [call.args[2] for call in send.await_args_list]
    assert "规则「24点」已关闭。" in sent_texts
    assert "规则已关闭" in sent_texts
    assert "不应该发送" not in sent_texts


@pytest.mark.asyncio
async def test_transfer_notice_module_rule_starts_game24_with_interaction_bot(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_load_game24_state", AsyncMock(return_value=None))
    monkeypatch.setattr(account_bot_runtime, "_save_game24_state", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "generate_24_puzzle", lambda: [1, 5, 5, 5])
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "game24-ticket",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "action": "module",
                        "module_key": "game24",
                        "module_prize": 888,
                        "response_template": "忽略",
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 7,
            "message": {
                "message_id": 70,
                "text": "转账成功\nAAA 射出 100\nBBB 接收 100",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert send.await_count == 1
    assert send.await_args.args[0] == "bbot-token"
    assert send.await_args.args[1] == -100123
    assert "24 点开始" in send.await_args.args[2]
    assert "奖金：888" in send.await_args.args[2]


@pytest.mark.asyncio
async def test_game24_winner_notice_replies_to_winning_answer(monkeypatch) -> None:
    send = AsyncMock()
    state = account_bot_runtime.Game24State(
        account_id=1,
        chat_id=-100123,
        numbers=[1, 5, 5, 5],
        prize=888,
        active=True,
        game_id="game24",
    )
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=9,
        user_id=111,
        chat_id=-100123,
        message_id=99,
        text="5*(5-1/5)",
        chat_type="supergroup",
        display_name="AAA",
    )
    monkeypatch.setattr(account_bot_runtime, "_load_game24_state", AsyncMock(return_value=state))
    monkeypatch.setattr(account_bot_runtime, "_claim_game24_winner", AsyncMock(return_value=True))
    monkeypatch.setattr(account_bot_service, "send_message", send)

    handled = await account_bot_runtime._try_handle_game24_answer(incoming)

    assert handled is True
    assert send.await_count == 1
    assert send.await_args.args[:3] == (
        "bbot-token",
        -100123,
        "答对了：AAA\n题目：24 点 [1 5 5 5]\n答案：5*(5-1/5) = 24\n奖金：888\n请由 userbot 账号人工回复赢家发放奖金。",
    )
    assert send.await_args.kwargs["reply_to_message_id"] == 99


@pytest.mark.asyncio
async def test_plus_amount_from_account_user_is_ignored_as_payout(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, key):  # noqa: ANN001
            if model is account_bot_runtime.Account and key == 1:
                return SimpleNamespace(tg_user_id=999)
            return None

    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_notice_config", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 5,
            "message": {
                "message_id": 50,
                "text": "+123",
                "from": {"id": 999, "first_name": "PayoutUser"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 49,
                    "from": {"id": 111, "first_name": "Winner"},
                    "text": "6",
                },
            },
        },
    )

    assert account_bot_service.get_transfer_notice_config.await_count == 0
    assert account_bot_service.get_transfer_bot_token.await_count == 0
    assert send.await_count == 0


@pytest.mark.asyncio
async def test_authorized_group_plain_text_does_not_fall_through_to_account_commands(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

    user = SimpleNamespace(enabled=True, role="admin", last_chat_id=None, display_name=None)
    handle_command = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock(return_value=user))
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value=account_bot_service.default_transfer_notice_config()),
    )
    monkeypatch.setattr(account_bot_runtime, "_handle_command", handle_command)
    monkeypatch.setattr(account_bot_service, "send_message", AsyncMock())

    await account_bot_runtime._handle_update(
        1,
        "bbot-token",
        {
            "update_id": 4,
            "message": {
                "message_id": 40,
                "text": "2",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert handle_command.await_count == 0
    assert account_bot_service.send_message.await_count == 0


@pytest.mark.asyncio
async def test_stop_interaction_bot_manager_does_not_stop_management_tasks() -> None:
    management_task = asyncio.create_task(asyncio.sleep(60))
    interaction_task = asyncio.create_task(asyncio.sleep(60))
    account_bot_runtime._TASKS[1] = management_task
    account_bot_runtime._INTERACTION_TASKS[1] = interaction_task
    try:
        assert account_bot_runtime.is_interaction_bot_running(1) is True

        await account_bot_runtime.stop_interaction_bot_manager()

        assert account_bot_runtime.is_interaction_bot_running(1) is False
        assert 1 in account_bot_runtime._TASKS
        assert not management_task.done()
        assert interaction_task.cancelled()
    finally:
        account_bot_runtime._TASKS.clear()
        account_bot_runtime._INTERACTION_TASKS.clear()
        management_task.cancel()
        await asyncio.gather(management_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_math_winner_notice_replies_to_winning_answer(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr(account_bot_service, "send_message", send)
    account_bot_runtime._MATH_GAMES[(1, -100123)] = account_bot_runtime.MathGameState(
        account_id=1,
        chat_id=-100123,
        question="7 - 1",
        answer=6,
        prize=123,
    )
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=5,
        user_id=111,
        chat_id=-100123,
        message_id=66,
        text="6",
        chat_type="supergroup",
        display_name="AAA",
    )

    handled = await account_bot_runtime._try_handle_math_answer(incoming)

    assert handled is True
    assert send.await_count == 1
    assert send.await_args.args[:3] == (
        "bbot-token",
        -100123,
        "答对了：AAA\n题目：7 - 1 = 6\n奖金：123\n请由 userbot 账号人工回复赢家发放奖金。",
    )
    assert send.await_args.kwargs["reply_to_message_id"] == 66


@pytest.mark.asyncio
async def test_math_game_state_survives_memory_clear_via_redis(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.claims: set[str] = set()

        async def set(self, key, value, *, ex=None, nx=False):  # noqa: ANN001
            assert ex == account_bot_runtime._MATH_GAME_TTL_SECONDS
            if nx:
                if key in self.claims:
                    return None
                self.claims.add(key)
                return True
            self.values[key] = value
            return True

        async def get(self, key):  # noqa: ANN001
            return self.values.get(key)

    redis = _Redis()
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.secrets, "token_hex", lambda _n: "game123")

    incoming_start = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=5,
        user_id=111,
        chat_id=-100123,
        message_id=55,
        text="转账成功\nAAA 射出 100\nBBB 接收 100",
        chat_type="supergroup",
        display_name="AAA",
    )
    await account_bot_runtime._start_math_game(incoming_start, prize=123)
    state = account_bot_runtime._math_state_from_payload(
        redis.values[account_bot_runtime._math_game_key(1, -100123)]
    )
    assert state is not None

    account_bot_runtime._MATH_GAMES.clear()
    incoming_answer = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=6,
        user_id=111,
        chat_id=-100123,
        message_id=66,
        text=str(state.answer),
        chat_type="supergroup",
        display_name="AAA",
    )

    handled = await account_bot_runtime._try_handle_math_answer(incoming_answer)

    assert handled is True
    assert send.await_args.kwargs["reply_to_message_id"] == 66


@pytest.mark.asyncio
async def test_math_winner_claim_only_sends_once(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.claimed = False

        async def set(self, key, value, *, ex=None, nx=False):  # noqa: ANN001
            if nx:
                if self.claimed:
                    return None
                self.claimed = True
                return True
            self.values[key] = value
            return True

        async def get(self, key):  # noqa: ANN001
            return self.values.get(key)

    redis = _Redis()
    send = AsyncMock()
    state = account_bot_runtime.MathGameState(
        account_id=1,
        chat_id=-100123,
        question="7 - 1",
        answer=6,
        prize=123,
        game_id="game123",
        active=True,
    )
    await redis.set(account_bot_runtime._math_game_key(1, -100123), account_bot_runtime.json.dumps(account_bot_runtime.asdict(state)), ex=900)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=5,
        user_id=111,
        chat_id=-100123,
        message_id=66,
        text="6",
        chat_type="supergroup",
        display_name="AAA",
    )
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)

    assert await account_bot_runtime._try_handle_math_answer(incoming) is True
    state.active = True
    await redis.set(account_bot_runtime._math_game_key(1, -100123), account_bot_runtime.json.dumps(account_bot_runtime.asdict(state)), ex=900)
    assert await account_bot_runtime._try_handle_math_answer(incoming) is True

    assert send.await_count == 1


def test_confirm_redis_key_uses_hash_not_plain_token() -> None:
    nonce = "plain-confirm-token"
    key = account_bot_runtime._confirm_redis_key(nonce)
    assert nonce not in key
    assert key.startswith("account_bot_confirm:")


@pytest.mark.asyncio
async def test_request_confirm_redis_only_stores_hashed_token(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, str]] = []

        async def setex(self, key: str, ttl: int, value: str) -> None:
            self.calls.append((key, ttl, value))

    redis = _Redis()
    nonce = "token-for-confirm"
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bot-token",
        update_id=1,
        user_id=1001,
        chat_id=2002,
        message_id=3003,
        text="/restart",
        callback_id=None,
        callback_data=None,
        display_name="tester",
    )
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime.secrets, "token_urlsafe", lambda n: nonce)
    monkeypatch.setattr(account_bot_runtime, "_send", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_audit_confirm_event", AsyncMock())

    await account_bot_runtime._request_confirm(incoming, "admin", "restart", "重启账号 worker")

    assert len(redis.calls) == 1
    key, ttl, value = redis.calls[0]
    assert ttl == 300
    assert nonce not in key
    assert nonce not in value


@pytest.mark.asyncio
async def test_confirm_action_token_can_only_be_consumed_once(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.value = '{"account_id":1,"tg_user_id":42,"action":"restart","payload":{}}'

        async def get(self, _key: str) -> str | None:
            return self.value

        async def getdel(self, _key: str) -> str | None:
            v = self.value
            self.value = None
            return v

    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bot-token",
        update_id=1,
        user_id=42,
        chat_id=1,
        message_id=2,
        text="",
        callback_id="cb-1",
        callback_data="ab:1:confirm:restart:nonce",
        display_name=None,
    )
    answer = AsyncMock()
    execute = AsyncMock()
    redis = _Redis()
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "answer_callback", answer)
    monkeypatch.setattr(account_bot_runtime, "_execute_confirmed_action", execute)
    monkeypatch.setattr(account_bot_runtime, "_audit_confirm_event", AsyncMock())

    await account_bot_runtime._confirm_action(incoming, "admin", "restart", "nonce")
    await account_bot_runtime._confirm_action(incoming, "admin", "restart", "nonce")

    assert execute.await_count == 1
    assert answer.await_count == 1
    assert answer.await_args.kwargs.get("text") == "确认已过期"


@pytest.mark.asyncio
async def test_confirm_action_expired_token_is_rejected(monkeypatch) -> None:
    class _Redis:
        async def get(self, _key: str) -> str | None:
            return None

        async def getdel(self, _key: str) -> str | None:
            return None

    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bot-token",
        update_id=1,
        user_id=42,
        chat_id=1,
        message_id=2,
        text="",
        callback_id="cb-1",
        callback_data="ab:1:confirm:restart:nonce",
        display_name=None,
    )
    answer = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _Redis())
    monkeypatch.setattr(account_bot_service, "answer_callback", answer)
    monkeypatch.setattr(account_bot_runtime, "_execute_confirmed_action", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_audit_confirm_event", AsyncMock())

    await account_bot_runtime._confirm_action(incoming, "admin", "restart", "nonce")

    assert answer.await_count == 1
    assert answer.await_args.kwargs.get("text") == "确认已过期"


@pytest.mark.asyncio
async def test_confirm_action_owner_mismatch_does_not_consume_token(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.value = '{"account_id":1,"tg_user_id":99,"action":"restart","payload":{}}'
            self.getdel_called = 0

        async def get(self, _key: str) -> str | None:
            return self.value

        async def getdel(self, _key: str) -> str | None:
            self.getdel_called += 1
            v = self.value
            self.value = None
            return v

    redis = _Redis()
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bot-token",
        update_id=1,
        user_id=42,
        chat_id=1,
        message_id=2,
        text="",
        callback_id="cb-1",
        callback_data="ab:1:confirm:restart:nonce",
        display_name=None,
    )
    answer = AsyncMock()
    execute = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "answer_callback", answer)
    monkeypatch.setattr(account_bot_runtime, "_execute_confirmed_action", execute)
    monkeypatch.setattr(account_bot_runtime, "_audit_confirm_event", AsyncMock())

    await account_bot_runtime._confirm_action(incoming, "admin", "restart", "nonce")

    assert redis.getdel_called == 0
    assert redis.value is not None
    assert execute.await_count == 0
    assert answer.await_count == 1
    assert answer.await_args.kwargs.get("text") == "只能由原用户确认"


@pytest.mark.asyncio
async def test_toggle_feature_operator_cannot_toggle_remote_plugin(monkeypatch) -> None:
    class _RemotePlugin:
        enabled = False

    class _DBResult:
        def scalar_one_or_none(self):
            return None

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, key):  # noqa: ANN001
            return type("FeatureRow", (), {"is_builtin": False, "display_name": "DemoPlugin"})()

        async def execute(self, _stmt):  # noqa: ANN001
            return _DBResult()

        async def commit(self):
            return None

    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bot-token",
        update_id=1,
        user_id=42,
        chat_id=1,
        message_id=2,
        text="",
        callback_id="cb-1",
        callback_data="ab:1:feature_toggle:demo",
        display_name=None,
    )

    answer = AsyncMock()
    show_features = AsyncMock()
    req_confirm = AsyncMock()
    set_feature = AsyncMock()
    enable_remote = AsyncMock()
    write_audit = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "answer_callback", answer)
    monkeypatch.setattr(account_bot_runtime, "_show_features", show_features)
    monkeypatch.setattr(account_bot_runtime, "_request_confirm", req_confirm)
    monkeypatch.setattr(account_bot_runtime.feature_service, "set_account_feature", set_feature)
    monkeypatch.setattr(account_bot_runtime.remote_plugin_service, "get_by_name", AsyncMock(return_value=_RemotePlugin()))
    monkeypatch.setattr(account_bot_runtime.remote_plugin_service, "enable", enable_remote)
    monkeypatch.setattr(account_bot_runtime.audit, "write", write_audit)

    await account_bot_runtime._toggle_feature(incoming, "operator", "demo")

    assert req_confirm.await_count == 0
    assert set_feature.await_count == 0
    assert enable_remote.await_count == 0
    assert write_audit.await_count == 0
    assert answer.await_count == 1
    assert answer.await_args.kwargs.get("show_alert") is True
    assert "仅 admin" in answer.await_args.kwargs.get("text")


@pytest.mark.asyncio
async def test_notify_runtime_log_deduplicates_same_error(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.keys: set[str] = set()

        async def set(self, key: str, _value: str, *, ex: int, nx: bool) -> bool | None:
            assert ex == account_bot_runtime._RUNTIME_NOTIFY_DEDUPE_TTL_SECONDS
            assert nx is True
            if key in self.keys:
                return None
            self.keys.add(key)
            return True

    redis = _Redis()
    notify = AsyncMock(return_value=1)
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime, "notify_account", notify)

    row = RuntimeLog(
        account_id=7,
        level="error",
        source="plugin",
        message="配置错误：x",
    )
    await account_bot_runtime.notify_runtime_log(row)
    await account_bot_runtime.notify_runtime_log(row)

    assert notify.await_count == 1
