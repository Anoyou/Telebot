"""账号绑定 Bot 联动系统的关键安全单测。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.db.models.account_bot import AccountBot
from app.schemas.account_bot import AccountBotConfigUpdate
from app.services import account_bot_runtime, account_bot_service


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
