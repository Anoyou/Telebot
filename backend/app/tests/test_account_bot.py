"""账号绑定 Bot 联动系统的关键安全单测。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.account_bot_defaults import DEFAULT_TRANSFER_NOTICE_TEMPLATE, LEGACY_TRANSFER_NOTICE_TEMPLATE
from app.api import account_bots
from app.db.models.account import Account
from app.db.models.account_bot import AccountBot
from app.db.models.log import RuntimeLog
from app.schemas.account_bot import AccountBotConfigUpdate, AccountBotTestRequest
from app.services import account_bot_runtime, account_bot_service, audit
from app.worker import runtime as worker_runtime
from app.worker.plugins import loader as plugin_loader
from app.worker.plugins.builtin.chatgpt_image.manifest import MANIFEST as CHATGPT_IMAGE_MANIFEST
from app.worker.plugins.builtin.codex_image.manifest import MANIFEST as CODEX_IMAGE_MANIFEST


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

    async def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            deleted += 1 if self.data.pop(key, None) is not None else 0
        return deleted

    async def keys(self, pattern: str):
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [key for key in self.data if key.startswith(prefix)]
        return [key for key in self.data if key == pattern]

    async def rpush(self, key: str, value: str | bytes):
        self.data.setdefault(key, "")
        return 1


@pytest.mark.asyncio
async def test_worker_pubsub_idle_timeout_is_treated_as_no_message() -> None:
    class _PubSub:
        async def get_message(self, **_kwargs):  # noqa: ANN003
            raise TimeoutError("idle")

    assert await worker_runtime._next_pubsub_message(_PubSub()) is None


def test_account_bot_config_response_hides_plain_token() -> None:
    """配置出参只暴露 has_token，不返回明文 token 或加密串。"""

    row = AccountBot(account_id=1, bot_token_enc="encrypted-placeholder")
    out = account_bot_service.config_to_response(row)

    assert out.has_token is True
    assert "token" not in out.model_dump()
    assert out.remote_plugin_policy.enabled is False
    assert out.remote_plugin_policy.install is False


def test_disabled_or_cleared_management_bot_hides_stale_conflict_error() -> None:
    row = AccountBot(
        account_id=1,
        enabled=False,
        status="disabled",
        bot_token_enc=None,
        last_error="Conflict: terminated by other getUpdates request",
    )

    out = account_bot_service.config_to_response(row)

    assert out.enabled is False
    assert out.has_token is False
    assert out.last_error is None


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


@pytest.mark.asyncio
async def test_account_bot_test_with_token_override_relies_on_polling(monkeypatch) -> None:
    monkeypatch.setattr(
        account_bot_service,
        "send_message",
        AsyncMock(return_value={"message_id": 123, "from": {"id": 456}}),
    )
    monkeypatch.setattr(account_bot_service, "sanitize_bot_error", lambda exc, *, token: str(exc))
    monkeypatch.setattr(audit, "write", AsyncMock())

    class _DB:
        async def commit(self):
            return None

    user = SimpleNamespace(id=100)
    resp = await account_bots.test_account_bot(
        1,
        AccountBotTestRequest(
            chat_id=-100123,
            text="测试联动消息",
            bot_token_override="123456:override-token",
        ),
        _DB(),
        user,
    )

    assert resp.ok is True
    assert resp.sent == 1
    assert "Bbot 将通过 polling 自然接收并触发联动" in (resp.message or "")
    account_bot_service.send_message.assert_awaited_once()


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
            "query_commands": [" 。玩法 ", "。玩法", "玩法菜单"],
            "receiver_text": " 我 ",
            "amount": "100",
            "response_template": " 检测到 {amount} ",
            "transfer_notice_template": " 测试到账\n付款人：{payer_name}\n收款人：{receiver_name}\n金额：{amount} ",
        }
    )

    assert cfg["enabled"] is True
    assert cfg["chat_id"] == -100123
    assert cfg["chat_ids"] == [-100123, -100999]
    assert cfg["trusted_bot_id"] == 456
    assert cfg["trigger_text"] == "转账成功"
    assert cfg["trigger_texts"] == ["转账成功", "交易成功"]
    assert cfg["query_commands"] == ["。玩法", "玩法菜单"]
    assert cfg["receiver_text"] == "我"
    assert cfg["amount"] == 100
    assert cfg["response_template"] == "检测到 {amount}"
    assert cfg["transfer_notice_template"] == "测试到账\n付款人：{payer_name}\n收款人：{receiver_name}\n金额：{amount}"
    assert cfg["rules"][0]["id"] == "legacy-default"
    assert cfg["rules"][0]["chat_ids"] == [-100123, -100999]
    assert cfg["rules"][0]["trigger_texts"] == ["转账成功", "交易成功"]


def test_account_bot_transfer_notice_config_upgrades_legacy_default_template() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {"transfer_notice_template": LEGACY_TRANSFER_NOTICE_TEMPLATE}
    )

    assert cfg["transfer_notice_template"] == DEFAULT_TRANSFER_NOTICE_TEMPLATE
    assert "language-转账成功" in cfg["transfer_notice_template"]


@pytest.mark.asyncio
async def test_clear_transfer_bot_token_also_clears_detected_bot_id() -> None:
    row = SimpleNamespace(
        value={
            "enabled": True,
            "trusted_bot_id": 456,
            "transfer_bot_token_enc": "old-token",
            "transfer_bot_id": 456,
            "rules": [
                {
                    "id": "default",
                    "name": "默认规则",
                    "enabled": True,
                    "trigger_texts": ["转账成功"],
                    "action": "notice",
                    "response_template": "收到 {amount}",
                }
            ],
        }
    )

    class _DB:
        async def get(self, model, _key):  # noqa: ANN001
            if model is Account:
                return SimpleNamespace(id=1)
            if model is account_bot_service.SystemSetting:
                return row
            return None

        async def flush(self):
            return None

        def add(self, _row):  # noqa: ANN001
            raise AssertionError("existing setting row should be updated in place")

    out = await account_bot_service.update_transfer_notice_config(
        _DB(),
        1,
        {"clear_transfer_bot_token": True},
    )

    assert row.value["transfer_bot_token_enc"] is None
    assert row.value["transfer_bot_id"] is None
    assert out["transfer_bot_id"] is None
    assert out["has_transfer_bot_token"] is False


@pytest.mark.asyncio
async def test_update_transfer_notice_requires_trusted_bot_for_payment_rules(monkeypatch) -> None:
    class _DB:
        async def get(self, *_args):  # noqa: ANN002
            return None

        def add(self, _row):  # noqa: ANN001
            return None

        async def flush(self):
            return None

    monkeypatch.setattr(account_bot_service, "ensure_account", AsyncMock())

    with pytest.raises(Exception) as exc_info:
        await account_bot_service.update_transfer_notice_config(
            _DB(),
            1,
            {
                "enabled": True,
                "rules": [
                    {
                        "id": "paid",
                        "enabled": True,
                        "trigger_mode": "payment",
                        "trigger_texts": ["转账成功"],
                    }
                ],
            },
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "TRUSTED_BOT_ID_REQUIRED"


@pytest.mark.asyncio
async def test_interaction_service_requires_trusted_bot_for_payment_rules(monkeypatch) -> None:
    from app.services import interaction_bot_service

    class _DB:
        async def get(self, *_args):  # noqa: ANN002
            return None

        def add(self, _row):  # noqa: ANN001
            return None

        async def flush(self):
            return None

    monkeypatch.setattr(account_bot_service, "ensure_account", AsyncMock())

    with pytest.raises(Exception) as exc_info:
        await interaction_bot_service.update_transfer_notice_config(
            _DB(),
            1,
            {
                "enabled": True,
                "rules": [
                    {
                        "id": "paid",
                        "enabled": True,
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                    }
                ],
            },
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "TRUSTED_BOT_ID_REQUIRED"


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


def test_keyword_rules_drop_hidden_payment_filters_but_keep_user_limits() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "rules": [
                {
                    "id": "pt",
                    "enabled": True,
                    "chat_ids": [-100123],
                    "trigger_mode": "keyword",
                    "trigger_texts": ["转账成功"],
                    "module_start_keywords": ["置顶 id=数字"],
                    "receiver_user_id": "111",
                    "receiver_text": "BBB",
                    "amount": "100",
                    "action": "module",
                    "module_key": "pt_promote",
                    "module_action": "promote_torrent",
                    "concurrency": "chat",
                    "user_cooldown_seconds": "6h",
                    "daily_limit_per_user": 2,
                }
            ],
        }
    )

    rule = cfg["rules"][0]
    assert rule["trigger_mode"] == "keyword"
    assert rule["amount"] is None
    assert rule["receiver_user_id"] is None
    assert rule["receiver_text"] is None
    assert rule["user_cooldown_seconds"] == "6h"
    assert rule["daily_limit_per_user"] == 2


def test_account_bot_math10_rule_gets_default_start_keywords() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "rules": [
                {
                    "id": "math10",
                    "enabled": True,
                    "trigger_mode": "payment",
                    "action": "math10",
                }
            ],
        }
    )

    rule = cfg["rules"][0]
    assert rule["trigger_mode"] == "both"
    assert rule["module_start_keywords"] == ["发十以内算数", "十以内算数", "开算数题"]


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


def test_account_bot_interaction_rule_infers_single_module_action() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "rules": [
                {
                    "id": "game24-ticket",
                    "enabled": True,
                    "chat_ids": ["-100123"],
                    "trigger_mode": "both",
                    "trigger_texts": ["转账成功"],
                    "module_start_keywords": ["开24点"],
                    "amount": "100",
                    "action": "module",
                    "module_key": "game24",
                    "module_prize": "888",
                }
            ],
        }
    )

    rule = cfg["rules"][0]
    assert rule["module_key"] == "game24"
    assert rule["module_action"] == "start_paid_game"
    assert rule["module_session_scope"] == "chat"


def test_interaction_rule_uses_declared_installed_entry_session_scope(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "new_game"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "new_game",
                "interaction_entries": [
                    {
                        "key": "start_new_game",
                        "title": "开始新游戏",
                        "session_scope": "chat",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(account_bot_service, "settings", SimpleNamespace(plugins_installed_path=tmp_path))

    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "rules": [
                {
                    "id": "new-game",
                    "enabled": True,
                    "trigger_mode": "keyword",
                    "module_start_keywords": ["开新游戏"],
                    "action": "module",
                    "module_key": "new_game",
                    "module_action": "start_new_game",
                    "concurrency": "user",
                    "user_cooldown_seconds": "6h",
                    "daily_limit_per_user": 2,
                }
            ],
        }
    )

    rule = cfg["rules"][0]
    assert rule["concurrency"] == "user"
    assert rule["module_session_scope"] == "chat"
    assert rule["user_cooldown_seconds"] == "6h"
    assert rule["daily_limit_per_user"] == 2


def test_account_bot_interaction_rule_strips_rule_owned_module_config() -> None:
    cfg = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "module_config": {"prize": 1, "timeout": 2, "theme": "dark"},
            "rules": [
                {
                    "id": "dice-ticket",
                    "enabled": True,
                    "action": "module",
                    "module_key": "dice_grid_hunt",
                    "module_config": {
                        "prize": 123,
                        "timeout": 500,
                        "valid_seconds": 600,
                        "theme": "classic",
                    },
                }
            ],
        }
    )

    assert cfg["module_config"] == {"theme": "classic"}
    assert cfg["rules"][0]["module_config"] == {"theme": "classic"}


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


def test_trusted_transfer_notice_sender_requires_configured_sender() -> None:
    assert account_bot_runtime._trusted_transfer_notice_sender_matches({}, 12345) is False
    assert account_bot_runtime._trusted_transfer_notice_sender_matches({"trusted_bot_id": 12345}, 12345) is True
    assert account_bot_runtime._trusted_transfer_notice_sender_matches({"trusted_bot_id": 12345}, 12346) is False
    assert account_bot_runtime._trusted_transfer_notice_sender_matches({"transfer_bot_id": 12345}, 12345) is True


@pytest.mark.asyncio
async def test_transfer_notice_hard_rejects_when_trusted_bot_ids_are_empty(monkeypatch) -> None:
    start_math = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", start_math)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": None,
                "transfer_bot_id": None,
                "rules": [
                    {
                        "id": "paid",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "payment",
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "action": "math10",
                    }
                ],
            }
        ),
    )

    handled = await account_bot_runtime._try_handle_transfer_notice(
        SimpleNamespace(),
        account_bot_runtime.Incoming(
            account_id=1,
            token="bbot-token",
            update_id=1,
            user_id=999999,
            chat_id=-100123,
            message_id=10,
            text="转账成功\nAAA 射出 100\nBBB 接收 100",
        ),
    )

    assert handled is False
    start_math.assert_not_awaited()


@pytest.mark.asyncio
async def test_interaction_update_ignores_message_from_interaction_bot_itself(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    transfer_notice = AsyncMock(return_value=False)
    command_or_keyword = AsyncMock(return_value=False)
    module_message = AsyncMock(return_value=False)
    math_answer = AsyncMock(return_value=False)
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "interaction_bot_id": 8807483916}),
    )
    monkeypatch.setattr(account_bot_runtime, "_try_handle_transfer_notice", transfer_notice)
    monkeypatch.setattr(account_bot_runtime, "_try_handle_interaction_rule_command_or_keyword", command_or_keyword)
    monkeypatch.setattr(account_bot_runtime, "_try_handle_interaction_module_message", module_message)
    monkeypatch.setattr(account_bot_runtime, "_try_handle_math_answer", math_answer)

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 88,
            "message": {
                "message_id": 880,
                "text": "转账成功\n奖金：888",
                "from": {"id": 8807483916, "is_bot": True, "first_name": "Bbot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    transfer_notice.assert_not_awaited()
    command_or_keyword.assert_not_awaited()
    module_message.assert_not_awaited()
    math_answer.assert_not_awaited()


def test_account_bot_transfer_notice_template_renders_parseable_notice() -> None:
    template = account_bot_service.default_transfer_notice_config()["transfer_notice_template"]

    notice = account_bot_runtime._render_transfer_bot_notice(
        template,
        "付款方",
        "收款方",
        88,
        payer_user_id=1122,
        receiver_user_id=9988,
    )

    assert notice == (
        '<pre><code class="language-转账成功">付款人：付款方\n'
        "付款人ID：1122\n"
        "收款人：收款方\n"
        "金额：88\n"
        "收款人ID：9988</code></pre>"
    )
    assert "language-转账成功" in notice
    assert account_bot_runtime._parse_transfer_notice(notice) == {
        "payer_name": "付款方",
        "payer_user_id": 1122,
        "receiver_name": "收款方",
        "amount": 88,
        "receiver_user_id": 9988,
    }


def test_format_user_name_uses_public_name_without_username() -> None:
    assert (
        account_bot_runtime._format_user_name(  # noqa: SLF001
            {"first_name": "你心里已经有答案了", "last_name": "", "username": "uhaveanswer"}
        )
        == "你心里已经有答案了"
    )
    assert account_bot_runtime._format_user_name({"first_name": "A", "last_name": "B", "username": "ab"}) == "A B"  # noqa: SLF001
    assert account_bot_runtime._format_user_name({"username": "only_username"}) is None  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template", "error_type"),
    [
        ("转账成功\n付款人：{payer_name}\n金额：{amount:.2f}\n收款人：{receiver_name}", "ValueError"),
        ("转账成功\n付款人：{payer_name\n收款人：{receiver_name}\n金额：{amount}", "ValueError"),
    ],
)
async def test_transfer_command_template_render_failure_falls_back_and_logs(
    monkeypatch,
    caplog,
    template: str,
    error_type: str,
) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    send = AsyncMock(return_value={})
    runtime_log = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(account_bot_runtime, "_write_interaction_runtime_log", runtime_log)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "chat_ids": [-100123],
                "transfer_notice_template": template,
            }
        ),
    )

    with caplog.at_level(logging.WARNING, logger=account_bot_runtime.__name__):
        await account_bot_runtime._handle_transfer_test_update(
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

    assert send.await_count == 1
    assert send.await_args.args[:3] == (
        "abot-token",
        -100123,
        '<pre><code class="language-转账成功">付款人：PayoutUser\n'
        "付款人ID：999\n"
        "收款人：Winner\n"
        "金额：123\n"
        "收款人ID：111</code></pre>",
    )
    assert runtime_log.await_count == 1
    assert runtime_log.await_args.args[1:] == (
        "warn",
        "转账通知模板渲染失败，已回退默认模板",
    )
    assert runtime_log.await_args.kwargs["error"].startswith(f"{error_type}:")
    assert runtime_log.await_args.kwargs["template"] == template
    assert "transfer notice template render failed aid=1 chat_id=-100123" in caplog.text
    assert error_type in caplog.text


def test_interaction_rule_keywords_match_exact_text_or_notice_line() -> None:
    rule = {"trigger_texts": ["转账成功"]}

    assert account_bot_runtime._rule_matches_trigger(rule, "转账成功\n付款人：AAA")
    assert account_bot_runtime._rule_matches_trigger(rule, "转账成功")
    assert account_bot_runtime._rule_matches_trigger(rule, "aa转账成功") is False
    assert account_bot_runtime._rule_matches_trigger(rule, "转账成功了") is False
    assert account_bot_runtime._message_equals_any("123", ["123"])
    assert account_bot_runtime._message_equals_any("123456", ["123"]) is False
    assert account_bot_runtime._message_equals_any("aa123", ["123"]) is False
    assert account_bot_runtime._message_match_keyword_pattern("置顶 id=12345", ["置顶 id=数字"]) == {"id": "12345"}
    assert account_bot_runtime._message_match_keyword_pattern("置顶 id = 12345", ["置顶 id = 数字"]) == {"id": "12345"}
    assert account_bot_runtime._message_match_keyword_pattern("猜骰 num=1000", ["猜骰 num=数字"]) == {"num": "1000"}
    assert account_bot_runtime._message_match_keyword_pattern("置顶 id=abc", ["置顶 id=数字"]) is None
    assert account_bot_runtime._message_match_keyword_pattern("置顶 id=1 id=2", ["置顶 id=数字 id=数字"]) is None


def test_interaction_payment_payload_preserves_payer_user_id() -> None:
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=10,
        user_id=456,
        chat_id=-100123,
        message_id=70,
        text="转账成功",
        display_name="TransferBot",
    )

    payload = account_bot_runtime._interaction_module_payload(
        incoming,
        {"id": "lotto", "module_action": "start_lottery_plus"},
        {"payer_name": "AAA", "payer_user_id": 111, "amount": 10003},
        event_type="payment_confirmed",
    )

    assert payload["payer_user_id"] == 111
    assert payload["payer_name"] == "AAA"
    assert payload["sender_user_id"] == 456
    assert payload["event"]["data"]["payer_user_id"] == 111
    assert payload["source"]["type"] == "payment_confirmed"
    assert payload["source"]["chat_id"] == -100123
    assert payload["actor"]["user_id"] == 456
    assert payload["trigger"]["entry_key"] == "start_lottery_plus"
    assert payload["session"]["scope"] == "chat"
    assert payload["session"]["ttl_seconds"] == 600


@pytest.mark.asyncio
async def test_resolve_payout_mode_accepts_math10_module_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "enabled": True,
                        "action": "module",
                        "module_key": "math10",
                        "chat_ids": [-100123],
                    }
                ],
            }
        ),
    )

    assert await account_bot_runtime._resolve_payout_mode(1, -100123) == "auto"


@pytest.mark.asyncio
async def test_resolve_payout_mode_accepts_dice_grid_module_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "enabled": True,
                        "action": "module",
                        "module_key": "dice_grid_hunt",
                        "chat_ids": [-100123],
                    }
                ],
            }
        ),
    )

    assert await account_bot_runtime._resolve_payout_mode(1, -100123) == "auto"


@pytest.mark.asyncio
async def test_interaction_payload_sets_auto_payout_mode_for_math10_module(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, key):  # noqa: ANN001
            if model is account_bot_runtime.Account and key == 1:
                return SimpleNamespace(tg_username="owner")
            return None

    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=10,
        user_id=456,
        chat_id=-100123,
        message_id=70,
        text="10",
        display_name="AAA",
    )
    rule = {
        "id": "math10-ticket",
        "action": "module",
        "module_key": "math10",
        "module_action": "start_math10",
        "chat_ids": [-100123],
    }
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [rule],
            }
        ),
    )

    payload = await account_bot_runtime._interaction_module_payload_async(
        incoming,
        rule,
        None,
        event_type="message",
    )

    assert payload["payout_account_label"] == "@owner"
    assert payload["payout_mode"] == "auto"
    assert payload["settlement"]["mode"] == "auto"
    assert payload["settlement"]["amount"] == 123
    assert payload["settlement"]["payout_account_label"] == "@owner"


def test_interaction_entry_manifest_normalizes_command_fallback() -> None:
    entry = account_bot_service.normalize_interaction_entry_manifest(
        {
            "key": "start_paid_game",
            "launch_mode": "hybrid",
            "session_scope": "chat",
            "events": ["payment_confirmed", "bad", "message"],
            "command_fallback": {"enabled": True, "command": "24d", "mode": "hint_only"},
            "result_contract": {"send_via": ["interaction_bot", "bad", "userbot_reply", "interaction_bot"]},
        }
    )

    assert entry is not None
    assert entry["launch_mode"] == "hybrid"
    assert entry["events"] == ["payment_confirmed", "message"]
    assert entry["preserve_command_trigger"] is True
    assert entry["command_fallback"] == {"enabled": True, "command": "24d", "mode": "hint_only"}
    assert entry["result_contract"]["send_via"] == ["interaction_bot", "userbot_reply"]


def test_rule_entry_allows_event_when_declared_events_missing_for_compatibility(monkeypatch) -> None:
    monkeypatch.setattr(
        account_bot_service,
        "declared_module_entry_events",
        lambda module_key, entry_key: [],
    )
    rule = {
        "id": "legacy-bridge",
        "action": "module",
        "module_key": "legacy_demo",
        "module_action": "start_legacy_demo",
    }

    assert account_bot_runtime._rule_entry_allows_event(rule, "payment_confirmed") is True
    assert account_bot_runtime._rule_entry_allows_event(rule, "message") is True
    assert account_bot_runtime._rule_entry_allows_event(rule, "session_close") is True


@pytest.mark.asyncio
async def test_payment_interaction_session_uses_payer_user_scope(monkeypatch) -> None:
    redis = _MemoryRedis()
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=10,
        user_id=456,
        chat_id=-100123,
        message_id=70,
        text="转账成功",
        display_name="TransferBot",
    )
    rule = {
        "id": "lotto-ticket",
        "module_key": "lottery_plus",
        "module_action": "start_lottery_plus",
        "module_session_scope": "user",
        "concurrency": "chat",
        "valid_seconds": 600,
    }

    await account_bot_runtime._save_interaction_session(
        incoming,
        rule,
        "payment_confirmed",
        {"payer_user_id": 111},
    )

    payer_key = account_bot_runtime._interaction_session_key(1, rule, -100123, 111)
    transfer_bot_key = account_bot_runtime._interaction_session_key(1, rule, -100123, 456)
    assert payer_key in redis.data
    assert transfer_bot_key not in redis.data
    assert '"started_by_user_id": 111' in redis.data[payer_key]
    assert '"source_user_id": 456' in redis.data[payer_key]


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
                "text": "转账成功\n付款人：路人A\n收款人：我的TG名\n金额：100",
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

    await account_bot_runtime._handle_transfer_test_update(
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
    assert send.await_count == 1
    assert send.await_args_list[0].args[:3] == (
        "abot-token",
        -100123,
        '<pre><code class="language-转账成功">付款人：AAA\n'
        "付款人ID：111\n"
        "收款人：BBB\n"
        "金额：254\n"
        "收款人ID：222</code></pre>",
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
                "trigger_text": "模拟到账",
                "receiver_text": "BBB",
                "amount": None,
                "action": "notice",
                "math_prize": 123,
                "response_template": "检测到 {payer_name} 向 {receiver_name} 转账 {amount}，已进入游戏流程。",
                "transfer_notice_template": "模拟到账\n付款人：{payer_name}\n收款人：{receiver_name}\n金额：{amount}",
            }
        ),
    )

    await account_bot_runtime._handle_transfer_test_update(
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
        "模拟到账\n付款人：AAA\n收款人：BBB\n金额：100",
    )
    assert send.await_args_list[0].kwargs["reply_to_message_id"] == 30


@pytest.mark.asyncio
async def test_plus_amount_notice_ignores_rule_amount_threshold(monkeypatch) -> None:
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
                "chat_ids": [-100123],
                "trusted_bot_id": 456,
                "transfer_notice_template": "转账成功\n{payer_name} 射出 {amount}\n{receiver_name} 接收 {amount}",
                "rules": [
                    {
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "payment",
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "amount_match_mode": "eq",
                        "action": "math10",
                    }
                ],
            }
        ),
    )

    await account_bot_runtime._handle_transfer_test_update(
        1,
        "bbot-token",
        {
            "update_id": 4,
            "message": {
                "message_id": 40,
                "text": "+88",
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

    assert send.await_args_list[0].args[:3] == (
        "abot-token",
        -100123,
        "转账成功\nAAA 射出 88\nBBB 接收 88",
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

    await account_bot_runtime._handle_transfer_test_update(
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
@pytest.mark.parametrize(
    ("rule_patch", "closed", "expected_send_count"),
    [
        ({"trigger_mode": "keyword"}, False, 1),
        ({"trigger_mode": "both"}, True, 1),
    ],
)
async def test_reply_plus_amount_notice_ignores_rule_trigger_mode_and_state(
    monkeypatch,
    rule_patch: dict[str, object],
    closed: bool,
    expected_send_count: int,
) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    rule = {
        "id": "game24-ticket",
        "enabled": True,
        "chat_ids": [-100123],
        "trigger_texts": ["转账成功"],
        "receiver_text": "BBB",
        "amount": 100,
        "action": "module",
        "module_key": "game24",
        "module_action": "start_paid_game",
        **rule_patch,
    }
    redis = _MemoryRedis()
    if closed:
        redis.data[account_bot_runtime._rule_state_key(1, rule, -100123)] = "closed"

    send = AsyncMock()
    get_transfer_bot_token = AsyncMock(return_value="abot-token")
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", get_transfer_bot_token)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "trusted_bot_id": 456, "rules": [rule]}),
    )

    await account_bot_runtime._handle_transfer_test_update(
        1,
        "bbot-token",
        {
            "update_id": 6,
            "message": {
                "message_id": 60,
                "text": "+100",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 59,
                    "from": {"id": 222, "first_name": "BBB"},
                    "text": "hello",
                },
            },
        },
    )

    assert send.await_count == expected_send_count
    assert get_transfer_bot_token.await_count == expected_send_count


@pytest.mark.asyncio
async def test_reply_plus_amount_emits_test_notice_without_triggering_module(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    send = AsyncMock()
    run_entry = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
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

    await account_bot_runtime._handle_transfer_test_update(
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

    assert send.await_count == 1
    assert send.await_args.args[:3] == (
        "abot-token",
        -100123,
        '<pre><code class="language-转账成功">付款人：AAA\n'
        "付款人ID：111\n"
        "收款人：B\n"
        "金额：100\n"
        "收款人ID：222</code></pre>",
    )
    assert run_entry.await_count == 0


@pytest.mark.asyncio
async def test_transfer_test_bot_ignores_plus_amount_to_configured_bots(monkeypatch) -> None:
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
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "chat_ids": [-100123],
                "interaction_bot_id": 8807483916,
                "transfer_bot_id": 8980553289,
                "trusted_bot_id": 8980553289,
                "rules": [{"enabled": True, "chat_ids": [-100123]}],
            }
        ),
    )

    await account_bot_runtime._handle_transfer_test_update(
        1,
        "abot-token",
        {
            "update_id": 81,
            "message": {
                "message_id": 81,
                "text": "+100",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 80,
                    "from": {"id": 8807483916, "first_name": "交互Bbot", "username": "TelePilotBbot", "is_bot": True},
                    "text": "hello",
                },
            },
        },
    )

    assert send.await_count == 0
    assert account_bot_service.get_transfer_bot_token.await_count == 0


@pytest.mark.asyncio
async def test_transfer_test_bot_only_emits_notice_without_starting_module(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    send = AsyncMock(side_effect=[{"from": {"id": 8980553289}, "message_id": 220}, {}])
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "骰子游戏已开始，奖金 123"}],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 8980553289,
                "rules": [
                    {
                        "id": "dice-ticket",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "receiver_user_id": 8629045843,
                        "receiver_text": "@uhavebnum",
                        "amount": 111,
                        "action": "module",
                        "module_key": "dice_grid_hunt",
                        "module_action": "start_dice_grid_hunt",
                        "module_prize": 123,
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_transfer_test_update(
        1,
        "bbot-token",
        {
            "update_id": 9,
            "message": {
                "message_id": 90,
                "text": "+111",
                "from": {"id": 1682400007, "first_name": "你心里已经有答案了", "username": "uhaveanswer"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 89,
                    "from": {"id": 8629045843, "first_name": "你心里没点数？", "username": "uhavebnum"},
                    "text": "开启游戏",
                },
            },
        },
    )

    assert send.await_count == 1
    assert send.await_args_list[0].args[:3] == (
        "abot-token",
        -100123,
        '<pre><code class="language-转账成功">付款人：你心里已经有答案了\n'
        "付款人ID：1682400007\n"
        "收款人：你心里没点数？\n"
        "金额：111\n"
        "收款人ID：8629045843</code></pre>",
    )
    assert run_entry.await_count == 0


@pytest.mark.asyncio
async def test_transfer_notice_update_uses_unescaped_notice_text(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    send = AsyncMock(side_effect=[{"from": {"id": 456}, "message_id": 220}, {}])
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "已开始"}],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
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
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "B&B",
                        "amount": 100,
                        "action": "module",
                        "module_key": "game24",
                        "module_action": "start_paid_game",
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 10,
            "message": {
                "message_id": 100,
                "text": "转账成功\n付款人：A&B\n付款人ID：111\n收款人：B&B\n金额：100\n收款人ID：222",
                "from": {"id": 456, "first_name": "Abot", "is_bot": True},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert run_entry.await_count == 1
    payload = run_entry.await_args.kwargs["payload"]
    assert payload["payer_name"] == "A&B"
    assert payload["receiver_name"] == "B&B"


@pytest.mark.asyncio
async def test_transfer_notice_uses_first_matching_interaction_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
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
async def test_transfer_notice_matches_html_code_language_marker(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="answer", display_name="答案", tg_user_id=999)
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
                "rules": [
                    {
                        "id": "math-language-marker",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "你心里已经有答案了",
                        "amount": 1,
                        "action": "math10",
                        "math_prize": 456,
                    },
                ],
            }
        ),
    )

    text = "Yy 射出 1 蝌蚪\n你心里已经有答案了 接收 1 蝌蚪"
    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 61,
            "message": {
                "message_id": 610,
                "text": text,
                "entities": [{"type": "pre", "offset": 0, "length": len(text), "language": "language-转账成功"}],
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert start_math.await_count == 1
    assert start_math.await_args.kwargs == {"prize": 456}
    assert send.await_count == 0


@pytest.mark.asyncio
async def test_transfer_notice_parses_amount_from_replied_message(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="answer", display_name="答案", tg_user_id=999)
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
                "rules": [
                    {
                        "id": "math-reply-transfer",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "你心里已经有答案了",
                        "amount": 1,
                        "action": "math10",
                        "math_prize": 456,
                    },
                ],
            }
        ),
    )

    transfer_text = "Yy 射出 1 蝌蚪\n你心里已经有答案了 接收 1 蝌蚪"
    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 62,
            "message": {
                "message_id": 620,
                "text": "转账成功",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
                "reply_to_message": {
                    "message_id": 619,
                    "text": transfer_text,
                    "from": {"id": 999, "is_bot": False, "first_name": "Yy"},
                },
            },
        },
    )

    assert start_math.await_count == 1
    assert start_math.await_args.kwargs == {"prize": 456}
    assert send.await_count == 0


@pytest.mark.asyncio
async def test_transfer_notice_skips_closed_rule_and_uses_next_open_match(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    closed_rule = {
        "id": "closed-notice",
        "enabled": True,
        "chat_ids": [-100123],
        "trigger_mode": "payment",
        "trigger_texts": ["转账成功"],
        "receiver_text": "BBB",
        "amount": 200,
        "action": "notice",
        "response_template": "不应该命中",
    }
    open_rule = {
        "id": "open-math",
        "enabled": True,
        "chat_ids": [-100123],
        "trigger_mode": "payment",
        "trigger_texts": ["转账成功"],
        "receiver_text": "BBB",
        "amount": 200,
        "action": "math10",
        "math_prize": 456,
        "response_template": "忽略",
    }
    redis = _MemoryRedis()
    redis.data[account_bot_runtime._rule_state_key(1, closed_rule, -100123)] = "closed"
    start_math = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
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
                "rules": [closed_rule, open_rule],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 62,
            "message": {
                "message_id": 620,
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
async def test_transfer_notice_blank_receiver_defaults_to_account_identity(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_user_id=999, tg_username="account_a", display_name="A")
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
                "rules": [
                    {
                        "id": "math-default-receiver",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": None,
                        "amount": 100,
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
            "update_id": 61,
            "message": {
                "message_id": 610,
                "text": "转账成功\nAAA 射出 100\nB 接收 100",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert start_math.await_count == 0
    assert send.await_count == 0


@pytest.mark.asyncio
async def test_transfer_notice_receiver_user_id_takes_priority(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    start_math = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", start_math)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "math-by-id",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_user_id": 222,
                        "receiver_text": "A",
                        "amount": 100,
                        "action": "math10",
                        "math_prize": 456,
                        "response_template": "忽略",
                    },
                ],
            }
        ),
    )

    for update_id, receiver_id in [(62, 333), (63, 222)]:
        await account_bot_runtime._handle_interaction_update(
            1,
            "bbot-token",
            {
                "update_id": update_id,
                "message": {
                    "message_id": update_id * 10,
                    "text": f"转账成功\nAAA 射出 100\nA 接收 100\n收款人ID：{receiver_id}",
                    "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                    "chat": {"id": -100123, "type": "supergroup"},
                },
            },
        )

    assert start_math.await_count == 1


@pytest.mark.asyncio
async def test_transfer_notice_receiver_user_id_only_requires_notice_id(monkeypatch) -> None:
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
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", start_math)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "math-by-id-only",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_user_id": 222,
                        "receiver_text": None,
                        "amount": 100,
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
            "update_id": 64,
            "message": {
                "message_id": 640,
                "text": "转账成功\nAAA 射出 100\nA 接收 100",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert start_math.await_count == 0


@pytest.mark.asyncio
async def test_transfer_notice_receiver_user_id_can_fallback_to_receiver_text_without_notice_id(monkeypatch) -> None:
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
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", start_math)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "math-by-id-or-name",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_user_id": 222,
                        "receiver_text": "A",
                        "amount": 100,
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
            "update_id": 65,
            "message": {
                "message_id": 650,
                "text": "转账成功\nAAA 射出 100\nA 接收 100",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert start_math.await_count == 1


@pytest.mark.asyncio
async def test_transfer_notice_accepts_detected_test_transfer_bot_id(monkeypatch) -> None:
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
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", start_math)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 999999,
                "transfer_bot_id": 456,
                "rules": [
                    {
                        "id": "paid-remote",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "@uhavebnum",
                        "amount": 111,
                        "action": "math10",
                        "math_prize": 456,
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 65,
            "message": {
                "message_id": 650,
                "text": (
                    "转账成功\n"
                    "你心里已经有答案了 (@uhaveanswer) 射出 111\n"
                    "你心里没点数？ (@uhavebnum) 接收 111\n"
                    "收款人ID：8629045843"
                ),
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert start_math.await_count == 1


@pytest.mark.asyncio
async def test_interaction_keyword_invokes_module_interaction_entry_and_payment_mode_ignores_notice(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "24 点开始\n奖金：321"}],
        )
    )
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_service, "send_message", send)
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
                        "module_action": "start_paid_game",
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
    assert run_entry.await_count == 0

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 992,
            "message": {
                "message_id": 9920,
                "text": "开24点123",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )
    assert run_entry.await_count == 0

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 993,
            "message": {
                "message_id": 9930,
                "text": "开24点",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )
    assert run_entry.await_count == 1
    assert run_entry.await_args.kwargs["plugin_key"] == "game24"
    assert run_entry.await_args.kwargs["entry_key"] == "start_paid_game"
    payload = run_entry.await_args.kwargs["payload"]
    assert payload["event_type"] == "keyword"
    assert payload["prize"] == 321
    assert send.await_args.args[:3] == ("bbot-token", -100123, "24 点开始\n奖金：321")


@pytest.mark.asyncio
async def test_interaction_keyword_user_cooldown_and_daily_limit_mark_only_after_success(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    rule = {
        "id": "pt-promote",
        "name": "置顶促销",
        "enabled": True,
        "chat_ids": [-100123],
        "trigger_mode": "keyword",
        "trigger_texts": ["转账成功"],
        "module_start_keywords": ["置顶 id=数字"],
        "action": "module",
        "module_key": "pt_promote",
        "module_action": "promote_torrent",
        "concurrency": "chat",
        "user_cooldown_seconds": "6h",
        "daily_limit_per_user": 2,
    }
    redis = _MemoryRedis()
    run_entry = AsyncMock(
        side_effect=[
            (False, "模块启动失败", []),
            (True, None, [{"type": "send_message", "text": "置顶成功"}, {"type": "no_session"}]),
        ]
    )
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "rules": [rule]}),
    )

    base_message = {
        "from": {"id": 111, "first_name": "Alice"},
        "chat": {"id": -100123, "type": "supergroup"},
    }
    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {"update_id": 1001, "message": {"message_id": 10010, "text": "置顶 id=12345", **base_message}},
    )
    assert run_entry.await_count == 1
    assert not any(key.startswith("account_bot:interaction_user_") for key in redis.data)

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {"update_id": 1002, "message": {"message_id": 10020, "text": "置顶 id=12345", **base_message}},
    )
    assert run_entry.await_count == 2
    assert send.await_args.args[:3] == ("bbot-token", -100123, "置顶成功")
    assert any(key.startswith("account_bot:interaction_user_cooldown:") for key in redis.data)
    assert any(key.startswith("account_bot:interaction_user_daily:") for key in redis.data)

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {"update_id": 1003, "message": {"message_id": 10030, "text": "置顶 id=12345", **base_message}},
    )
    assert run_entry.await_count == 2
    assert "今日已成功置顶促销 1/2 次" in send.await_args.args[2]
    assert "距离下次可用 CD 还剩 6小时" in send.await_args.args[2]


@pytest.mark.asyncio
async def test_interaction_keyword_module_result_false_does_not_mark_usage(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    rule = {
        "id": "pt-promote",
        "name": "置顶促销",
        "enabled": True,
        "chat_ids": [-100123],
        "trigger_mode": "keyword",
        "trigger_texts": ["转账成功"],
        "module_start_keywords": ["置顶 id=数字"],
        "action": "module",
        "module_key": "pt_promote",
        "module_action": "promote_torrent",
        "concurrency": "user",
        "user_cooldown_seconds": "6h",
        "daily_limit_per_user": 2,
    }
    redis = _MemoryRedis()
    send = AsyncMock()
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [
                {"type": "send_message", "text": "ℹ️ ID 为 12345 的种子已处于置顶状态，本次不再处理。"},
                {"type": "result", "success": False},
                {"type": "no_session"},
            ],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "rules": [rule]}),
    )

    message = {
        "from": {"id": 111, "first_name": "Alice"},
        "chat": {"id": -100123, "type": "supergroup"},
    }
    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {"update_id": 1001, "message": {"message_id": 10010, "text": "置顶 id=12345", **message}},
    )

    assert run_entry.await_count == 1
    assert "本次不再处理" in send.await_args.args[2]
    assert not any(key.startswith("account_bot:interaction_user_cooldown:") for key in redis.data)
    assert not any(key.startswith("account_bot:interaction_user_daily:") for key in redis.data)


@pytest.mark.asyncio
async def test_interaction_module_start_text_is_sent_before_module_actions(monkeypatch) -> None:
    send = AsyncMock(return_value={"message_id": 88})
    edit = AsyncMock(return_value={"message_id": 88})
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "模块已开始"}],
        )
    )
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "edit_message", edit)
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=111,
        chat_id=-100123,
        message_id=10,
        text="开局",
        display_name="AAA",
    )

    ok = await account_bot_runtime._start_interaction_module(
        incoming,
        {
            "id": "module",
            "name": "互动模块",
            "module_key": "game24",
            "module_action": "start_paid_game",
            "module_start_text": "正在启动互动模块...",
        },
    )

    assert ok is True
    send.assert_awaited_once()
    assert send.await_args.args[:3] == ("bbot-token", -100123, "正在启动互动模块...")
    assert send.await_args.kwargs["reply_to_message_id"] == 10
    edit.assert_awaited_once()
    assert edit.await_args.args[:4] == ("bbot-token", -100123, 88, "模块已开始")


@pytest.mark.asyncio
async def test_interaction_module_can_request_no_session(monkeypatch) -> None:
    send = AsyncMock()
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "下注成功"}, {"type": "end_session"}],
        )
    )
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)

    ok = await account_bot_runtime._start_interaction_module(
        account_bot_runtime.Incoming(
            account_id=1,
            token="bbot-token",
            update_id=1,
            user_id=111,
            chat_id=-100123,
            message_id=10,
            text="转账成功",
            display_name="AAA",
        ),
        {
            "id": "lotto",
            "name": "彩票",
            "module_key": "lottery_plus",
            "module_action": "start_lottery_plus",
        },
        parsed={"amount": 10003},
        event_type="payment_confirmed",
    )

    assert ok is False
    assert send.await_count == 1
    assert send.await_args.args[:3] == ("bbot-token", -100123, "下注成功")


@pytest.mark.asyncio
async def test_execute_interaction_rule_skips_module_when_event_not_declared(monkeypatch) -> None:
    run_module = AsyncMock(return_value=(True, True))
    monkeypatch.setattr(account_bot_runtime, "_run_interaction_module", run_module)
    monkeypatch.setattr(
        account_bot_service,
        "declared_module_entry_events",
        lambda module_key, entry_key: ["keyword", "message"] if module_key == "pt_promote" and entry_key == "promote_torrent" else [],
    )

    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=11,
        user_id=111,
        chat_id=-100123,
        message_id=99,
        text="转账成功",
        display_name="AAA",
    )
    rule = {
        "id": "pt-promote",
        "name": "置顶促销",
        "action": "module",
        "module_key": "pt_promote",
        "module_action": "promote_torrent",
    }

    executed = await account_bot_runtime._execute_interaction_rule(
        incoming,
        rule,
        {"id": "12345"},
        event_type="payment_confirmed",
    )

    assert executed is False
    run_module.assert_not_awaited()


@pytest.mark.asyncio
async def test_interaction_keyword_cannot_bypass_paid_threshold(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    run_entry = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "paid-keyword-game24",
                        "name": "24点门票",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "module_start_keywords": ["开24点"],
                        "amount": 100,
                        "action": "module",
                        "module_key": "game24",
                        "module_action": "start_paid_game",
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
            "update_id": 9921,
            "message": {
                "message_id": 99210,
                "text": "开24点",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert run_entry.await_count == 0
    assert send.await_count == 1
    assert send.await_args.args[2] == "该24点门票是付费娱乐模块，请对收款人：@owner的任意消息回复+100即可参与。"


@pytest.mark.asyncio
async def test_interaction_closed_rule_only_replies_to_keyword(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_user_id=111, tg_username="owner", display_name="Owner")
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
    assert any(text.startswith("规则「24点」已关闭，") for text in sent_texts)
    assert "规则已关闭" in sent_texts
    assert "不应该发送" not in sent_texts


@pytest.mark.asyncio
async def test_interaction_rule_open_close_commands_require_account_owner(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_user_id=999, tg_username="owner", display_name="Owner")
            return None

    redis = _MemoryRedis()
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "game-rule",
                        "name": "游戏",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "keyword",
                        "module_start_keywords": ["我要游戏"],
                        "close_commands": ["关闭游戏"],
                        "disabled_message": "规则已关闭",
                        "action": "math10",
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 996,
            "message": {
                "message_id": 9960,
                "text": "关闭游戏",
                "from": {"id": 111, "first_name": "User"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )
    assert send.await_count == 0

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 997,
            "message": {
                "message_id": 9970,
                "text": "关闭游戏",
                "from": {"id": 999, "first_name": "Owner"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    sent_texts = [call.args[2] for call in send.await_args_list]
    assert any(text.startswith("规则「游戏」已关闭，") for text in sent_texts)


@pytest.mark.asyncio
async def test_math10_builtin_action_starts_from_default_keyword(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    start_math = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_runtime, "_start_math_game", start_math)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value=account_bot_service.normalize_transfer_notice_config(
                {
                    "enabled": True,
                    "rules": [
                        {
                            "id": "math10",
                            "enabled": True,
                            "chat_ids": [-100123],
                            "trigger_mode": "payment",
                            "action": "math10",
                            "math_prize": 456,
                        }
                    ],
                }
            )
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 998,
            "message": {
                "message_id": 9980,
                "text": "发十以内算数",
                "from": {"id": 111, "first_name": "User"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert start_math.await_count == 1
    assert start_math.await_args.kwargs == {"prize": 456}


@pytest.mark.asyncio
async def test_math10_module_rule_falls_back_when_worker_plugin_not_loaded(monkeypatch) -> None:
    from app.worker.plugins.builtin.math10 import plugin as math10_plugin

    monkeypatch.setattr(math10_plugin, "_new_math_question", lambda: ("3 + 4", 7))
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_runtime, "_load_account_holder_label", AsyncMock(return_value="@owner"))
    monkeypatch.setattr(account_bot_runtime, "_resolve_payout_mode", AsyncMock(return_value="manual"))
    monkeypatch.setattr(
        account_bot_runtime,
        "_run_worker_interaction_entry",
        AsyncMock(return_value=(False, "RuntimeError: 模块未加载或未启用：math10", [])),
    )
    send = AsyncMock(return_value={"message_id": 88})
    edit = AsyncMock(return_value={"message_id": 88})
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "edit_message", edit)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=456,
        chat_id=-100123,
        message_id=10,
        text="开算数题",
        display_name="AAA",
    )

    ok, keep_session = await account_bot_runtime._run_interaction_module(
        incoming,
        {
            "id": "math10-module",
            "name": "算数题",
            "action": "module",
            "module_key": "math10",
            "module_action": "start_math_game",
            "module_prize": 456,
            "module_start_text": "正在启动互动模块...",
        },
        event_type="keyword",
    )

    assert ok is True
    assert keep_session is True
    send.assert_awaited_once()
    assert send.await_args.args[:3] == ("bbot-token", -100123, "正在启动互动模块...")
    edit.assert_awaited_once()
    assert edit.await_args.args[:4] == (
        "bbot-token",
        -100123,
        88,
        "算数题测试开始\n题目：3 + 4 = ?\n奖金：456\n直接发送数字答案，答对后我会公告赢家。",
    )


@pytest.mark.asyncio
async def test_interaction_query_command_lists_current_chat_games(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value=account_bot_service.normalize_transfer_notice_config(
                {
                    "enabled": True,
                    "query_commands": ["玩法菜单"],
                    "rules": [
                        {
                            "id": "dice",
                            "name": "九宫格",
                            "enabled": True,
                            "chat_ids": [-100123],
                            "trigger_mode": "both",
                            "trigger_texts": ["转账成功"],
                            "module_start_keywords": ["。ct num=数字"],
                            "receiver_text": "owner",
                            "amount": 123,
                            "amount_match_mode": "eq",
                            "action": "module",
                            "module_key": "dice_grid_hunt",
                            "module_action": "start_dice_grid_hunt",
                            "module_prize": 123,
                            "user_cooldown_seconds": "10s",
                            "daily_limit_per_user": 2,
                            "valid_seconds": 90,
                        },
                        {
                            "id": "math",
                            "name": "其他群算数题",
                            "enabled": True,
                            "chat_ids": [-100999],
                            "trigger_mode": "keyword",
                            "module_start_keywords": ["算数题"],
                            "action": "math10",
                            "math_prize": 88,
                        },
                        {
                            "id": "notice",
                            "name": "通知",
                            "enabled": True,
                            "chat_ids": [-100123],
                            "action": "notice",
                        },
                    ],
                }
            )
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 1001,
            "message": {
                "message_id": 10010,
                "text": "玩法菜单",
                "from": {"id": 111, "first_name": "User"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert send.await_count == 1
    text = send.await_args.args[2]
    assert "当前可用联动玩法" in text
    assert "九宫格" in text
    assert "dice_grid_hunt" in text
    assert "。ct num=数字" in text
    assert "金额 = <code>123</code>" in text
    assert "每用户 CD <code>10s</code>" in text
    assert "每用户日上限 <code>2</code>" in text
    assert "其他群算数题" not in text
    assert "<b>通知</b>" not in text
    assert send.await_args.kwargs["reply_to_message_id"] == 10010


@pytest.mark.asyncio
async def test_interaction_query_command_ignores_uncovered_chat(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value=account_bot_service.normalize_transfer_notice_config(
                {
                    "enabled": True,
                    "query_commands": ["玩法菜单"],
                    "rules": [
                        {
                            "id": "dice",
                            "name": "九宫格",
                            "enabled": True,
                            "chat_ids": [-100123],
                            "trigger_mode": "keyword",
                            "module_start_keywords": ["。ct num=数字"],
                            "action": "module",
                            "module_key": "dice_grid_hunt",
                            "module_action": "start_dice_grid_hunt",
                        }
                    ],
                }
            )
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 1002,
            "message": {
                "message_id": 10020,
                "text": "玩法菜单",
                "from": {"id": 111, "first_name": "User"},
                "chat": {"id": -100999, "type": "supergroup"},
            },
        },
    )

    assert send.await_count == 0


@pytest.mark.asyncio
async def test_worker_suppresses_interaction_bot_owned_keyword(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "rules": [
                        {
                            "id": "remote-game",
                            "enabled": True,
                            "chat_ids": [-100123],
                            "module_start_keywords": ["我要猜骰"],
                            "open_commands": ["开启游戏"],
                            "close_commands": ["关闭游戏"],
                        }
                    ],
                }
            )

    state = SimpleNamespace(account_id=1)
    event = SimpleNamespace(chat_id=-100123, raw_text="我要猜骰")
    monkeypatch.setattr(plugin_loader, "AsyncSessionLocal", lambda: _DB())

    assert await plugin_loader._interaction_bot_owns_incoming_text(state, event) is True


@pytest.mark.asyncio
async def test_worker_suppresses_interaction_keyword_with_empty_chat_scope(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "rules": [
                        {
                            "id": "global-keyword",
                            "enabled": True,
                            "chat_ids": [],
                            "module_start_keywords": ["我要猜骰"],
                        }
                    ],
                }
            )

    state = SimpleNamespace(account_id=1)
    event = SimpleNamespace(chat_id=-100456, raw_text="我要猜骰")
    monkeypatch.setattr(plugin_loader, "AsyncSessionLocal", lambda: _DB())

    assert await plugin_loader._interaction_bot_owns_incoming_text(state, event) is True


@pytest.mark.asyncio
async def test_interaction_plain_message_routes_to_worker_entry_as_message(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "收到答案"}],
        )
    )
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "_load_interaction_session", AsyncMock(return_value={"rule_id": "dice-active"}))
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "dice-active",
                        "enabled": True,
                        "chat_ids": [-100777],
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "module_start_keywords": ["开始猜骰"],
                        "open_commands": ["开启猜骰"],
                        "close_commands": ["关闭猜骰"],
                        "status_commands": ["猜骰状态"],
                        "action": "module",
                        "module_key": "dice_grid_hunt",
                        "module_action": "answer_dice_grid_hunt",
                        "module_prize": 456,
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 1366,
            "message": {
                "message_id": 13660,
                "text": "我选第 3 格",
                "from": {"id": 111, "first_name": "AAA", "username": "aaa"},
                "chat": {"id": -100777, "type": "supergroup"},
            },
        },
    )

    assert run_entry.await_count == 1
    assert run_entry.await_args.kwargs["plugin_key"] == "dice_grid_hunt"
    assert run_entry.await_args.kwargs["entry_key"] == "answer_dice_grid_hunt"
    payload = run_entry.await_args.kwargs["payload"]
    assert payload["event_type"] == "message"
    assert payload["event"]["type"] == "message"
    assert payload["message_text"] == "我选第 3 格"
    assert payload["sender_user_id"] == 111
    assert payload["sender_username"] == "aaa"
    assert payload["message_id"] == 13660
    assert send.await_args.args[:3] == ("bbot-token", -100777, "收到答案")


@pytest.mark.asyncio
async def test_interaction_plain_message_skips_entries_without_message_event(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "收到答案"}],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "_load_interaction_session", AsyncMock(return_value={"rule_id": "pt-promote"}))
    monkeypatch.setattr(account_bot_service, "send_message", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "declared_module_entry_events",
        lambda module_key, entry_key: ["keyword"] if module_key == "pt_promote" and entry_key == "promote_torrent" else [],
    )
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "pt-promote",
                        "enabled": True,
                        "chat_ids": [-100777],
                        "trigger_mode": "keyword",
                        "trigger_texts": ["转账成功"],
                        "module_start_keywords": ["置顶 id=数字"],
                        "action": "module",
                        "module_key": "pt_promote",
                        "module_action": "promote_torrent",
                        "module_prize": 456,
                        "concurrency": "user",
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 1367,
            "message": {
                "message_id": 13670,
                "text": "继续置顶 12345",
                "from": {"id": 111, "first_name": "AAA", "username": "aaa"},
                "chat": {"id": -100777, "type": "supergroup"},
            },
        },
    )

    run_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_interaction_plain_message_control_action_clears_session(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    rule = {
        "id": "game24-active",
        "enabled": True,
        "chat_ids": [-100777],
        "trigger_mode": "both",
        "trigger_texts": ["转账成功"],
        "action": "module",
        "module_key": "game24",
        "module_action": "start_paid_game",
    }
    redis = _MemoryRedis()
    session_key = account_bot_runtime._interaction_session_key(1, rule, -100777)
    redis.data[session_key] = account_bot_runtime.json.dumps({"rule_id": "game24-active", "started_by_user_id": 111})
    run_entry = AsyncMock(return_value=(True, None, [{"type": "send_message", "text": "结束"}, {"type": "end_session"}]))
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "rules": [rule]}),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 13671,
            "message": {
                "message_id": 136710,
                "text": "5*(5-1/5)",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100777, "type": "supergroup"},
            },
        },
    )

    assert session_key not in redis.data
    assert send.await_args.args[:3] == ("bbot-token", -100777, "结束")


@pytest.mark.asyncio
async def test_chat_scoped_module_session_survives_user_usage_limits(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    rule = account_bot_service.normalize_transfer_notice_config(
        {
            "enabled": True,
            "rules": [
                {
                    "id": "dice-active",
                    "enabled": True,
                    "chat_ids": [-100777],
                    "trigger_mode": "keyword",
                    "module_start_keywords": ["我要猜骰"],
                    "action": "module",
                    "module_key": "dice_grid_hunt",
                    "module_action": "start_dice_grid_hunt",
                    "module_prize": 456,
                    "valid_seconds": 90,
                    "concurrency": "user",
                    "user_cooldown_seconds": "6h",
                    "daily_limit_per_user": 2,
                }
            ],
        }
    )["rules"][0]
    redis = _MemoryRedis()
    run_entry = AsyncMock(
        side_effect=[
            (
                True,
                None,
                [
                    {
                        "type": "send_photo",
                        "photo_base64": base64.b64encode(b"png").decode("ascii"),
                        "filename": "dice_grid_hunt.png",
                        "caption": "开局",
                    }
                ],
            ),
            (True, None, [{"type": "send_message", "text": "答对"}, {"type": "end_session"}]),
        ]
    )
    send_photo = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_service, "send_photo_bytes", send_photo)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "rules": [rule]}),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 13673,
            "message": {
                "message_id": 136730,
                "text": "我要猜骰",
                "from": {"id": 111, "first_name": "Starter"},
                "chat": {"id": -100777, "type": "supergroup"},
            },
        },
    )
    session_key = account_bot_runtime._interaction_session_key(1, rule, -100777)

    assert rule["concurrency"] == "user"
    assert rule["module_session_scope"] == "chat"
    assert session_key in redis.data
    assert not any(key.startswith("account_bot:interaction_session:") and ":user:" in key for key in redis.data)

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 13674,
            "message": {
                "message_id": 136740,
                "text": "3",
                "from": {"id": 222, "first_name": "Player"},
                "chat": {"id": -100777, "type": "supergroup"},
            },
        },
    )

    assert run_entry.await_count == 2
    assert run_entry.await_args_list[1].kwargs["payload"]["event_type"] == "message"
    assert run_entry.await_args_list[1].kwargs["payload"]["sender_user_id"] == 222
    assert session_key not in redis.data
    assert send.await_args.args[:3] == ("bbot-token", -100777, "答对")


@pytest.mark.asyncio
async def test_interaction_close_command_clears_user_scoped_sessions(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_user_id=999, tg_username="owner", display_name="Owner")
            return None

    rule = {
        "id": "user-game24",
        "name": "用户局",
        "enabled": True,
        "chat_ids": [-100778],
        "close_commands": ["关闭用户局"],
        "action": "module",
        "module_key": "game24",
        "module_action": "start_paid_game",
        "concurrency": "user",
    }
    other_rule = {
        "id": "other-game",
        "name": "其他局",
        "enabled": True,
        "chat_ids": [-100778],
        "close_commands": ["关闭其他局"],
        "action": "module",
        "module_key": "dice_grid_hunt",
        "module_action": "answer_dice_grid_hunt",
        "concurrency": "user",
    }
    redis = _MemoryRedis()
    key_111 = account_bot_runtime._interaction_session_key(1, rule, -100778, 111)
    key_222 = account_bot_runtime._interaction_session_key(1, rule, -100778, 222)
    other_key = account_bot_runtime._interaction_session_key(1, other_rule, -100778, 333)
    redis.data[key_111] = "session-111"
    redis.data[key_222] = "session-222"
    redis.data[other_key] = "session-333"
    send = AsyncMock()
    run_entry = AsyncMock(return_value=(True, None, []))
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "rules": [rule, other_rule]}),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 13672,
            "message": {
                "message_id": 136720,
                "text": "关闭用户局",
                "from": {"id": 999, "first_name": "Owner"},
                "chat": {"id": -100778, "type": "supergroup"},
            },
        },
    )

    assert key_111 not in redis.data
    assert key_222 not in redis.data
    assert other_key in redis.data
    assert run_entry.await_count == 2
    assert all(call.kwargs["payload"]["event_type"] == "session_close" for call in run_entry.await_args_list)
    assert "已结束 2 个进行中的游戏。" in send.await_args.args[2]


@pytest.mark.asyncio
async def test_close_interaction_rules_skips_session_close_for_entries_without_declared_event(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    rule = {
        "id": "pt-promote",
        "enabled": True,
        "chat_ids": [-100778],
        "trigger_mode": "keyword",
        "trigger_texts": ["转账成功"],
        "action": "module",
        "module_key": "pt_promote",
        "module_action": "promote_torrent",
        "close_commands": ["关闭置顶"],
        "concurrency": "user",
    }
    redis = _MemoryRedis()
    session_key = account_bot_runtime._interaction_session_key(1, rule, -100778, 111)
    redis.data[session_key] = account_bot_runtime.json.dumps({"rule_id": "pt-promote", "started_by_user_id": 111})
    send = AsyncMock()
    run_entry = AsyncMock(return_value=(True, None, [{"type": "send_message", "text": "结束"}, {"type": "end_session"}]))
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "_is_account_user_sender", AsyncMock(return_value=True))
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "declared_module_entry_events",
        lambda module_key, entry_key: ["keyword", "message"] if module_key == "pt_promote" and entry_key == "promote_torrent" else [],
    )
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(return_value={"enabled": True, "rules": [rule]}),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 13673,
            "message": {
                "message_id": 136730,
                "text": "关闭置顶",
                "from": {"id": 999, "first_name": "Owner"},
                "chat": {"id": -100778, "type": "supergroup"},
            },
        },
    )

    assert session_key not in redis.data
    run_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_interaction_control_and_start_keywords_do_not_route_as_plain_messages(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_user_id=111, tg_username="aaa", display_name="AAA")
            return None

    run_entry = AsyncMock()
    run_module = AsyncMock(return_value=(True, True))
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "_run_interaction_module", run_module)
    monkeypatch.setattr(account_bot_runtime, "_close_active_interaction_games", AsyncMock(return_value=0))
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "dice-active",
                        "name": "猜骰",
                        "enabled": True,
                        "chat_ids": [-100778],
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "module_start_keywords": ["开始猜骰"],
                        "open_commands": ["开启猜骰"],
                        "close_commands": ["关闭猜骰"],
                        "status_commands": ["猜骰状态"],
                        "action": "module",
                        "module_key": "dice_grid_hunt",
                        "module_action": "answer_dice_grid_hunt",
                    },
                ],
            }
        ),
    )

    for update_id, text in [
        (1367, "开启猜骰"),
        (1368, "猜骰状态"),
        (1369, "关闭猜骰"),
        (1370, "开始猜骰"),
    ]:
        await account_bot_runtime._handle_interaction_update(
            1,
            "bbot-token",
            {
                "update_id": update_id,
                "message": {
                    "message_id": update_id * 10,
                    "text": text,
                    "from": {"id": 111, "first_name": "AAA"},
                    "chat": {"id": -100778, "type": "supergroup"},
                },
            },
        )

    assert run_entry.await_count == 0
    assert run_module.await_count == 1
    assert run_module.await_args.args[0].text == "开始猜骰"


@pytest.mark.asyncio
async def test_paid_module_start_keyword_is_blocked_but_answer_message_routes(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "答案已记录"}],
        )
    )
    send = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "_load_interaction_session", AsyncMock(return_value={"rule_id": "paid-dice"}))
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "paid-dice",
                        "name": "付费猜骰",
                        "enabled": True,
                        "chat_ids": [-100779],
                        "trigger_mode": "both",
                        "trigger_texts": ["转账成功"],
                        "module_start_keywords": ["开始付费猜骰"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "action": "module",
                        "module_key": "dice_grid_hunt",
                        "module_action": "answer_dice_grid_hunt",
                        "module_prize": 789,
                    },
                ],
            }
        ),
    )

    for update_id, text in [
        (1371, "开始付费猜骰"),
        (1372, "42"),
    ]:
        await account_bot_runtime._handle_interaction_update(
            1,
            "bbot-token",
            {
                "update_id": update_id,
                "message": {
                    "message_id": update_id * 10,
                    "text": text,
                    "from": {"id": 111, "first_name": "AAA"},
                    "chat": {"id": -100779, "type": "supergroup"},
                },
            },
        )

    sent_texts = [call.args[2] for call in send.await_args_list]
    assert any("该付费猜骰是付费娱乐模块，请对收款人：BBB的任意消息回复+100即可参与。" == text for text in sent_texts)
    assert run_entry.await_count == 1
    payload = run_entry.await_args.kwargs["payload"]
    assert payload["event_type"] == "message"
    assert payload["event"]["type"] == "message"
    assert payload["message_text"] == "42"
    assert payload["prize"] == 789
    assert sent_texts[-1] == "答案已记录"


@pytest.mark.asyncio
async def test_transfer_notice_module_rule_starts_game24_with_interaction_bot(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    send = AsyncMock()
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "24 点开始\n奖金：888"}],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
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
                        "module_action": "start_paid_game",
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

    assert run_entry.await_count == 1
    assert run_entry.await_args.kwargs["plugin_key"] == "game24"
    assert run_entry.await_args.kwargs["entry_key"] == "start_paid_game"
    payload = run_entry.await_args.kwargs["payload"]
    assert payload["event_type"] == "payment_confirmed"
    assert payload["amount"] == 100
    assert payload["prize"] == 888
    assert send.await_count == 1
    assert send.await_args.args[:3] == ("bbot-token", -100123, "24 点开始\n奖金：888")


@pytest.mark.asyncio
async def test_transfer_notice_module_rule_counts_payer_daily_limit(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            return None

        async def get(self, *_args):  # noqa: ANN002
            return None

    redis = _MemoryRedis()
    send = AsyncMock()
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "置顶成功"}, {"type": "no_session"}],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "pt-promote",
                        "name": "置顶促销",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_mode": "payment",
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "action": "module",
                        "module_key": "pt_promote",
                        "module_action": "promote_torrent",
                        "concurrency": "user",
                        "daily_limit_per_user": 1,
                    },
                ],
            }
        ),
    )

    for update_id, message_id in [(71, 710), (72, 720)]:
        await account_bot_runtime._handle_interaction_update(
            1,
            "bbot-token",
            {
                "update_id": update_id,
                "message": {
                    "message_id": message_id,
                    "text": "转账成功\n付款人：AAA\n付款人ID：111\n收款人：BBB\n金额：100",
                    "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                    "chat": {"id": -100123, "type": "supergroup"},
                },
            },
        )

    assert run_entry.await_count == 1
    assert send.await_args.args[:3] == (
        "bbot-token",
        -100123,
        "AAA 今日已成功置顶促销 1/1 次，当日无法再次使用。",
    )


@pytest.mark.asyncio
async def test_transfer_notice_module_rule_invokes_remote_interaction_entry(monkeypatch) -> None:
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
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "远程猜数字已开始，奖金 777"}],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "guess-ticket",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "action": "module",
                        "module_key": "guess_number",
                        "module_action": "start_game",
                        "module_prize": 777,
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 71,
            "message": {
                "message_id": 710,
                "text": "转账成功\nAAA 射出 100\nBBB 接收 100",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert run_entry.await_count == 1
    assert run_entry.await_args.kwargs["plugin_key"] == "guess_number"
    assert run_entry.await_args.kwargs["entry_key"] == "start_game"
    assert run_entry.await_args.kwargs["payload"]["amount"] == 100
    assert run_entry.await_args.kwargs["payload"]["prize"] == 777
    assert send.await_args.args[:3] == (
        "bbot-token",
        -100123,
        "远程猜数字已开始，奖金 777",
    )


@pytest.mark.asyncio
async def test_transfer_notice_module_rule_accepts_guess_number_legacy_entry(monkeypatch) -> None:
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
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [{"type": "send_message", "text": "旧入口猜数字已开始，奖金 666"}],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime.audit, "write", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "trusted_bot_id": 456,
                "rules": [
                    {
                        "id": "guess-ticket",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "receiver_text": "BBB",
                        "amount": 100,
                        "action": "module",
                        "module_key": "guess_number",
                        "module_action": "start_game",
                        "module_prize": 666,
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 71,
            "message": {
                "message_id": 710,
                "text": "转账成功\nAAA 射出 100\nBBB 接收 100",
                "from": {"id": 456, "is_bot": True, "first_name": "Abot"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert run_entry.await_count == 1
    assert run_entry.await_args.kwargs["plugin_key"] == "guess_number"
    assert run_entry.await_args.kwargs["entry_key"] == "start_game"
    assert run_entry.await_args.kwargs["payload"]["prize"] == 666


@pytest.mark.asyncio
async def test_interaction_action_can_send_photo(monkeypatch) -> None:
    send = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(account_bot_service, "send_photo_bytes", send)
    monkeypatch.setattr(account_bot_service, "delete_message", delete)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=456,
        chat_id=-100123,
        message_id=10,
        text="",
    )

    await account_bot_runtime._apply_interaction_actions(
        incoming,
        [
            {
                "type": "send_photo",
                "photo_base64": base64.b64encode(b"png-bytes").decode("ascii"),
                "filename": "grid.png",
                "caption": "九宫格",
                "reply_to_message_id": 9,
            }
        ],
        replace_message_id=77,
    )

    assert send.await_count == 1
    assert send.await_args.args[:3] == ("bbot-token", -100123, b"png-bytes")
    assert send.await_args.kwargs == {
        "filename": "grid.png",
        "caption": "九宫格",
        "reply_to_message_id": 9,
    }
    delete.assert_awaited_once_with("bbot-token", -100123, 77)


@pytest.mark.asyncio
async def test_interaction_action_send_via_bbot_notice_and_logs_settlement(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    send = AsyncMock()
    delete = AsyncMock()
    write_log = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "delete_message", delete)
    monkeypatch.setattr(account_bot_runtime, "_write_interaction_runtime_log", write_log)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=456,
        chat_id=-100123,
        message_id=10,
        text="",
    )

    await account_bot_runtime._apply_interaction_actions(
        incoming,
        [
            {
                "type": "send_message",
                "text": "结算公告",
                "send_via": "bbot_notice",
                "reply_to_message_id": 9,
                "settlement": {
                    "mode": "manual",
                    "amount": 123,
                    "winner_user_id": 111,
                    "status": "announced",
                },
            }
        ],
        replace_message_id=77,
    )

    assert send.await_count == 1
    assert send.await_args.args[:3] == ("abot-token", -100123, "结算公告")
    assert send.await_args.kwargs["reply_to_message_id"] == 9
    delete.assert_awaited_once_with("bbot-token", -100123, 77)
    assert write_log.await_count == 1
    assert write_log.await_args_list[0].args[1:3] == ("info", "interaction settlement reported")
    assert write_log.await_args_list[0].kwargs["settlement"]["amount"] == 123


@pytest.mark.asyncio
async def test_interaction_action_send_via_userbot_reply_uses_worker_rpc_and_logs_settlement(monkeypatch) -> None:
    run_action = AsyncMock(return_value=(True, None, {"message_id": 91}))
    write_log = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_action", run_action)
    monkeypatch.setattr(account_bot_runtime, "_write_interaction_runtime_log", write_log)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=456,
        chat_id=-100123,
        message_id=10,
        text="",
    )

    await account_bot_runtime._apply_interaction_actions(
        incoming,
        [
            {
                "type": "send_message",
                "text": "账号本人回复",
                "send_via": "userbot_reply",
                "reply_to_message_id": 9,
                "settlement": {
                    "mode": "manual",
                    "amount": 123,
                    "winner_user_id": 111,
                    "winner_name": "AAA",
                    "status": "announced",
                },
            }
        ],
    )

    assert run_action.await_count == 1
    payload = run_action.await_args.kwargs["payload"]
    assert payload["action_type"] == "send_message"
    assert payload["chat_id"] == -100123
    assert payload["reply_to_message_id"] == 9
    assert write_log.await_count == 1
    assert write_log.await_args_list[0].args[1:3] == ("info", "interaction settlement reported")


@pytest.mark.asyncio
async def test_interaction_action_unknown_type_writes_runtime_log(monkeypatch) -> None:
    class _Redis:
        def __init__(self) -> None:
            self.items: list[str | bytes] = []

        async def rpush(self, key: str, value: str | bytes):
            self.items.append(value)
            return len(self.items)

    redis = _Redis()
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=456,
        chat_id=-100123,
        message_id=10,
        text="",
    )

    await account_bot_runtime._apply_interaction_actions(incoming, [{"type": "wait_answer"}])

    assert len(redis.items) == 1
    payload = redis.items[0].decode("utf-8") if isinstance(redis.items[0], bytes) else redis.items[0]
    assert "runtime_log_stream" not in payload
    assert "unsupported type=wait_answer" in payload
    assert '"level":"info"' in payload


@pytest.mark.asyncio
async def test_run_worker_interaction_entry_returns_timeout(monkeypatch) -> None:
    class _PubSub:
        def __init__(self) -> None:
            self.subscribed: list[str] = []
            self.unsubscribed: list[str] = []
            self.closed = False

        async def subscribe(self, channel: str) -> None:
            self.subscribed.append(channel)

        async def get_message(self, **_kwargs):  # noqa: ANN003
            return None

        async def unsubscribe(self, channel: str) -> None:
            self.unsubscribed.append(channel)

        async def close(self) -> None:
            self.closed = True

    class _Redis:
        def __init__(self) -> None:
            self.pubsub_obj = _PubSub()
            self.published: list[tuple[str, str]] = []

        def pubsub(self):
            return self.pubsub_obj

        async def publish(self, channel: str, payload: str) -> int:
            self.published.append((channel, payload))
            return 1

    redis = _Redis()
    timeout = account_bot_runtime._INTERACTION_ENTRY_TIMEOUT_SECONDS
    ticks = iter([0.0, 0.0, timeout + 1.0])
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime.time, "time", lambda: next(ticks, 6.0))
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=456,
        chat_id=-100123,
        message_id=10,
        text="",
    )

    ok, error, actions = await account_bot_runtime._run_worker_interaction_entry(
        incoming,
        plugin_key="game24",
        entry_key="start_paid_game",
        payload={"chat_id": -100123},
    )

    assert ok is False
    assert error == "worker 调用超时"
    assert actions == []
    assert redis.published
    assert redis.pubsub_obj.unsubscribed == redis.pubsub_obj.subscribed
    assert redis.pubsub_obj.closed is True


@pytest.mark.asyncio
async def test_run_worker_interaction_entry_waits_for_slow_plugin_reply(monkeypatch) -> None:
    class _PubSub:
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        async def subscribe(self, _channel: str) -> None:
            return None

        async def get_message(self, **_kwargs):  # noqa: ANN003
            self.calls += 1
            if self.calls == 1:
                return None
            return {
                "data": account_bot_runtime.make_cmd(
                    account_bot_runtime.CMD_RUN_INTERACTION_ENTRY,
                    ok=True,
                    error=None,
                    actions=[
                        {"type": "send_message", "text": "置顶成功"},
                        {"type": "result", "success": True},
                        {"type": "no_session"},
                    ],
                )
            }

        async def unsubscribe(self, _channel: str) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    class _Redis:
        def __init__(self) -> None:
            self.pubsub_obj = _PubSub()

        def pubsub(self):
            return self.pubsub_obj

        async def publish(self, _channel: str, _payload: str) -> int:
            return 1

    redis = _Redis()
    ticks = iter([0.0, 0.0, 6.0])
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    monkeypatch.setattr(account_bot_runtime.time, "time", lambda: next(ticks, 6.0))
    incoming = account_bot_runtime.Incoming(
        account_id=1,
        token="bbot-token",
        update_id=1,
        user_id=456,
        chat_id=-100123,
        message_id=10,
        text="",
    )

    ok, error, actions = await account_bot_runtime._run_worker_interaction_entry(
        incoming,
        plugin_key="pt_promote",
        entry_key="promote_torrent",
        payload={"chat_id": -100123, "id": "134100"},
    )

    assert ok is True
    assert error is None
    assert actions[0]["text"] == "置顶成功"
    assert actions[1] == {"type": "result", "success": True}
    assert redis.pubsub_obj.closed is True


def test_builtin_image_modules_stay_utility_without_interaction_entries() -> None:
    assert CODEX_IMAGE_MANIFEST.category == "utility"
    assert CODEX_IMAGE_MANIFEST.interaction_entries == []
    assert CHATGPT_IMAGE_MANIFEST.category == "utility"
    assert CHATGPT_IMAGE_MANIFEST.interaction_entries == []


@pytest.mark.asyncio
async def test_game24_winner_notice_replies_to_winning_answer(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, model, *_args):  # noqa: ANN002
            if model is Account:
                return SimpleNamespace(tg_username="owner", display_name="Owner", tg_user_id=999)
            return None

    send = AsyncMock()
    run_entry = AsyncMock(
        return_value=(
            True,
            None,
            [
                {
                    "type": "send_message",
                    "text": "答对了：AAA\n题目：24 点 [1 5 5 5]\n答案：5*(5-1/5) = 24\n奖金：888\n奖金将由 @owner 账号自动发放。",
                    "reply_to_message_id": 99,
                }
            ],
        )
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: _MemoryRedis())
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_runtime, "_load_interaction_session", AsyncMock(return_value={"rule_id": "game24-ticket"}))
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "rules": [
                    {
                        "id": "game24-ticket",
                        "enabled": True,
                        "chat_ids": [-100123],
                        "trigger_texts": ["转账成功"],
                        "action": "module",
                        "module_key": "game24",
                        "module_action": "start_paid_game",
                        "module_prize": 888,
                    },
                ],
            }
        ),
    )

    await account_bot_runtime._handle_interaction_update(
        1,
        "bbot-token",
        {
            "update_id": 9,
            "message": {
                "message_id": 99,
                "text": "5*(5-1/5)",
                "from": {"id": 111, "first_name": "AAA"},
                "chat": {"id": -100123, "type": "supergroup"},
            },
        },
    )

    assert run_entry.await_count == 1
    assert run_entry.await_args.kwargs["plugin_key"] == "game24"
    assert run_entry.await_args.kwargs["entry_key"] == "start_paid_game"
    payload = run_entry.await_args.kwargs["payload"]
    assert payload["event_type"] == "message"
    assert payload["message_text"] == "5*(5-1/5)"
    assert payload["payout_account_label"] == "@owner"
    assert payload["payout_mode"] == "auto"
    assert send.await_count == 1
    assert send.await_args.args[:3] == (
        "bbot-token",
        -100123,
        "答对了：AAA\n题目：24 点 [1 5 5 5]\n答案：5*(5-1/5) = 24\n奖金：888\n奖金将由 @owner 账号自动发放。",
    )
    assert send.await_args.kwargs["reply_to_message_id"] == 99


@pytest.mark.asyncio
async def test_transfer_test_bot_accepts_plus_amount_from_account_user(monkeypatch) -> None:
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

    send = AsyncMock(side_effect=[{"from": {"id": 456}, "message_id": 55}, {}])
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock())
    monkeypatch.setattr(
        account_bot_service,
        "get_transfer_notice_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "chat_ids": [-100123],
                "trusted_bot_id": 456,
                "trigger_texts": ["转账成功"],
                "receiver_text": None,
                "transfer_notice_template": "转账成功\n{payer_name} 射出 {amount}\n{receiver_name} 接收 {amount}",
            }
        ),
    )
    monkeypatch.setattr(account_bot_service, "get_transfer_bot_token", AsyncMock(return_value="abot-token"))

    await account_bot_runtime._handle_transfer_test_update(
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

    assert account_bot_service.get_transfer_bot_token.await_count == 1
    assert send.await_args_list[0].args[:3] == (
        "abot-token",
        -100123,
        "转账成功\nPayoutUser 射出 123\nWinner 接收 123",
    )
    assert send.await_args_list[0].kwargs["reply_to_message_id"] == 50


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
    monkeypatch.setattr(account_bot_runtime, "_load_account_holder_label", AsyncMock(return_value="@owner"))
    monkeypatch.setattr(account_bot_runtime, "_resolve_payout_mode", AsyncMock(return_value="manual"))
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
        display_name="<AAA & BBB>",
    )

    handled = await account_bot_runtime._try_handle_math_answer(incoming)

    assert handled is True
    assert send.await_count == 1
    assert send.await_args.args[:3] == (
        "bbot-token",
        -100123,
        "答对了：&lt;AAA &amp; BBB&gt;\n题目：7 - 1 = 6\n奖金：123\n请由 @owner 人工回复赢家发放奖金。",
    )
    assert send.await_args.kwargs["reply_to_message_id"] == 66


@pytest.mark.asyncio
async def test_math_winner_notice_uses_auto_payout_copy(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr(account_bot_service, "send_message", send)
    monkeypatch.setattr(account_bot_runtime, "_load_account_holder_label", AsyncMock(return_value="@owner"))
    monkeypatch.setattr(account_bot_runtime, "_resolve_payout_mode", AsyncMock(return_value="auto"))
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
        "答对了：AAA\n题目：7 - 1 = 6\n奖金：123\n奖金将由 @owner 账号自动发放。",
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
