from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.api import webhooks as webhooks_api
from app.crypto import decrypt_str
from app.db.models.account import Account
from app.db.models.rate_limit import ACTION_KEYS
from app.db.models.system import SystemSetting
from app.deps import get_current_user, get_db
from app.main import app
from app.services import rate_limit_service
from app.worker.ipc import CMD_WEBHOOK_DELIVER
from app.worker.plugins import loader as loader_mod
from app.worker.plugins.base import Plugin, PluginContext
from app.worker.plugins.manifest import Manifest

_REAL_INGRESS_LIMITER = webhooks_api._enforce_webhook_ingress_rate_limit

CSRF_HEADERS = {
    "X-Requested-With": "telepilot-ui",
    "X-CSRF-Token": "test-token",
    "Cookie": "csrf_token=test-token",
}


class _FakeDB:
    def __init__(
        self,
        *,
        token: str = "secret",
        hook_key: str = "default",
        configured: bool = True,
    ) -> None:
        self.accounts = {1: SimpleNamespace(id=1, template_id=None)}
        self.settings: dict[str, SystemSetting] = {}
        if configured:
            self.settings[webhooks_api._setting_key(1)] = SystemSetting(
                key=webhooks_api._setting_key(1),
                value={
                    "token": token,
                    "hooks": [{"key": hook_key, "label": hook_key, "enabled": True}],
                    "created_at": "2026-07-10T00:00:00+00:00",
                    "updated_at": "2026-07-10T00:00:00+00:00",
                },
            )
        self.added: list[Any] = []
        self.commits = 0
        self.get_calls: list[tuple[Any, Any]] = []

    async def get(self, model: Any, key: Any) -> Any:
        self.get_calls.append((model, key))
        table = getattr(model, "__tablename__", "")
        if model is Account or table == "account":
            return self.accounts.get(int(key))
        if model is SystemSetting or table == "system_setting":
            return self.settings.get(str(key))
        return None

    def add(self, row: Any) -> None:
        self.added.append(row)
        if isinstance(row, SystemSetting):
            self.settings[row.key] = row

    async def commit(self) -> None:
        self.commits += 1


class _FakeRedis:
    pass


@pytest.fixture(autouse=True)
def _allow_webhook_ingress(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        webhooks_api,
        "_enforce_webhook_ingress_rate_limit",
        AsyncMock(return_value=None),
    )


@pytest.fixture(autouse=True)
def _enable_webhooks_capability():
    """公开 webhook 投递依赖进程内能力缓存；测试默认开启。"""

    from app.services import platform_capabilities as platform_caps

    platform_caps._reset_for_tests()
    platform_caps._DESIRED["webhooks"] = True
    platform_caps._CACHE_READY = True
    yield
    platform_caps._reset_for_tests()


@asynccontextmanager
async def _client(db: _FakeDB):
    previous = dict(app.dependency_overrides)

    async def _override_db():
        yield db

    async def _override_user():
        return SimpleNamespace(id=7, username="tester")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _headers(token: str) -> dict[str, str]:
    return {
        **CSRF_HEADERS,
        webhooks_api.TOKEN_HEADER: token,
        "Content-Type": "application/json",
        "User-Agent": "pytest-webhook",
    }


@pytest.mark.asyncio
async def test_deliver_webhook_rejects_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB(token="good-token")
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", AsyncMock())

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers=_headers("bad-token"),
            json={"event": "demo"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_TOKEN_INVALID"
    assert db.commits == 0
    assert db.settings[webhooks_api._setting_key(1)].value["token"] == "good-token"
    webhooks_api.publish_cmd_with_ack_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_webhook_without_config_does_not_create_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB(configured=False)
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", AsyncMock())

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers=_headers("bad-token"),
            json={"event": "demo"},
        )

    assert response.status_code == 401
    assert db.commits == 0
    assert db.added == []
    assert db.settings == {}


@pytest.mark.asyncio
async def test_management_access_migrates_legacy_plaintext_token() -> None:
    db = _FakeDB(token="legacy-token")

    config = await webhooks_api._get_or_create_config(db, 1)

    assert "token" not in config
    assert decrypt_str(config["token_enc"]) == "legacy-token"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_ingress_limit_rejects_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB()
    limiter = AsyncMock(side_effect=HTTPException(status_code=429, detail={"code": "limited"}))
    monkeypatch.setattr(webhooks_api, "_enforce_webhook_ingress_rate_limit", limiter)

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers=_headers("good-token"),
            json={"event": "demo"},
        )

    assert response.status_code == 429
    assert db.get_calls == []


@pytest.mark.asyncio
async def test_deliver_webhook_module_disabled_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭 webhooks 模块后，最前层 404：不访问 DB、不限流、不投递。"""

    from app.services import platform_capabilities as platform_caps

    platform_caps._DESIRED["webhooks"] = False
    platform_caps._CACHE_READY = True
    db = _FakeDB(token="good-token")
    publish = AsyncMock()
    ingress = AsyncMock()
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", publish)
    monkeypatch.setattr(webhooks_api, "_enforce_webhook_ingress_rate_limit", ingress)

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers=_headers("good-token"),
            json={"event": "demo"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WEBHOOK_MODULE_DISABLED"
    assert db.get_calls == []
    ingress.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingress_limit_uses_hashed_client_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    class _Buckets:
        async def check_and_consume(self, account_id: int, action: str, *limits: Any, **kwargs: Any):
            captured.append(
                {"account_id": account_id, "action": action, "limits": limits, "kwargs": kwargs}
            )
            # 兜底桶放行、主桶拒绝，验证两层都会执行且 429 语义不变。
            allowed = action == webhooks_api.WEBHOOK_INGRESS_BACKSTOP_RATE_LIMIT_ACTION
            return allowed, 3, 1

    monkeypatch.setattr(webhooks_api, "TokenBuckets", lambda _redis: _Buckets())
    request = Request(
        {
            "type": "http",
            "headers": [],
            "method": "POST",
            "path": "/api/webhooks/1/default",
            "client": ("203.0.113.8", 12345),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await _REAL_INGRESS_LIMITER(request, _FakeRedis(), 1)

    assert exc_info.value.status_code == 429
    # 兜底桶（仅按 IP，限额放大）先于主桶执行。
    assert captured[0]["action"] == webhooks_api.WEBHOOK_INGRESS_BACKSTOP_RATE_LIMIT_ACTION
    assert captured[0]["limits"][:2] == (
        webhooks_api.INGRESS_LIMITS["per_second"] * webhooks_api.INGRESS_BACKSTOP_MULTIPLIER,
        webhooks_api.INGRESS_LIMITS["per_minute"] * webhooks_api.INGRESS_BACKSTOP_MULTIPLIER,
    )
    assert captured[0]["account_id"] != 0
    # 主桶按 (client_ip, account_id) 分桶：反代后同 IP 的不同账号互不挤占。
    assert captured[1]["action"] == webhooks_api.WEBHOOK_INGRESS_RATE_LIMIT_ACTION
    assert captured[1]["account_id"] != 0
    assert captured[1]["account_id"] != captured[0]["account_id"]
    assert captured[1]["limits"][:2] == (
        webhooks_api.INGRESS_LIMITS["per_second"],
        webhooks_api.INGRESS_LIMITS["per_minute"],
    )


@pytest.mark.asyncio
async def test_ingress_limit_buckets_accounts_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 IP 下不同账号必须落到不同主桶，单个高频发送方不能饿死其它账号。"""
    seen: set[int] = set()

    class _Buckets:
        async def check_and_consume(self, account_id: int, action: str, *limits: Any, **kwargs: Any):
            if action == webhooks_api.WEBHOOK_INGRESS_RATE_LIMIT_ACTION:
                seen.add(account_id)
            return True, 0, 1

    monkeypatch.setattr(webhooks_api, "TokenBuckets", lambda _redis: _Buckets())
    base = {
        "type": "http",
        "headers": [],
        "method": "POST",
        "path": "/api/webhooks/1/default",
        "client": ("198.51.100.7", 12345),
    }

    await _REAL_INGRESS_LIMITER(Request(base), _FakeRedis(), 1)
    await _REAL_INGRESS_LIMITER(Request(base), _FakeRedis(), 2)

    assert len(seen) == 2


@pytest.mark.asyncio
async def test_ingress_limit_fails_closed_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Buckets:
        async def check_and_consume(self, *_args: Any, **_kwargs: Any):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(webhooks_api, "TokenBuckets", lambda _redis: _Buckets())
    request = Request(
        {
            "type": "http",
            "headers": [],
            "method": "POST",
            "path": "/api/webhooks/1/default",
            "client": ("203.0.113.9", 12345),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await _REAL_INGRESS_LIMITER(request, _FakeRedis(), 1)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "WEBHOOK_RATE_LIMIT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_deliver_webhook_exempt_from_csrf_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """外部系统无法携带 X-Requested-With/CSRF cookie，webhook 路径必须豁免 CSRF 中间件，
    请求应穿过中间件到达端点做 token 校验（而非被 403 CSRF_HEADER_REQUIRED 拦截）。"""
    db = _FakeDB(token="good-token")
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", AsyncMock())

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers={  # 故意不带 CSRF_HEADERS，模拟第三方外部系统
                webhooks_api.TOKEN_HEADER: "bad-token",
                "Content-Type": "application/json",
                "User-Agent": "external-system",
            },
            json={"event": "demo"},
        )

    # 关键断言：没有被 CSRF 中间件 403 拦截，而是到达端点返回 token 校验结果
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_deliver_webhook_rejects_query_token_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB(token="good-token")
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", AsyncMock())

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default?token=good-token",
            headers={"Content-Type": "application/json"},
            json={"event": "demo"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_TOKEN_INVALID"
    webhooks_api.publish_cmd_with_ack_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_webhook_token_requires_csrf_header() -> None:
    db = _FakeDB(token="good-token")

    async with _client(db) as client:
        response = await client.post("/api/webhooks/1/token/reset")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_HEADER_REQUIRED"
    assert db.settings[webhooks_api._setting_key(1)].value["token"] == "good-token"


@pytest.mark.asyncio
async def test_deliver_webhook_unknown_account_does_not_oracle_account_existence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB(token="good-token")
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", AsyncMock())

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/999/default",
            headers={
                webhooks_api.TOKEN_HEADER: "bad-token",
                "Content-Type": "application/json",
            },
            json={"event": "demo"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_TOKEN_INVALID"
    webhooks_api.publish_cmd_with_ack_payload.assert_not_awaited()


def test_webhook_deliver_is_configurable_rate_limit_action() -> None:
    assert webhooks_api.WEBHOOK_RATE_LIMIT_ACTION in ACTION_KEYS
    assert rate_limit_service.default_for(webhooks_api.WEBHOOK_RATE_LIMIT_ACTION) == {
        "per_second": 2,
        "per_minute": 60,
        "per_hour": 1000,
        "per_day": 5000,
    }


@pytest.mark.asyncio
async def test_deliver_webhook_rejects_unknown_hook_key(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB(token="good-token", hook_key="default")
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", AsyncMock())

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/missing",
            headers=_headers("good-token"),
            json={"event": "demo"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WEBHOOK_HOOK_NOT_FOUND"
    webhooks_api.publish_cmd_with_ack_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_webhook_returns_429_when_bucket_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB(token="good-token", hook_key="default")
    monkeypatch.setattr(webhooks_api, "get_redis", lambda: _FakeRedis())

    async def _limited(*_args: Any, **_kwargs: Any) -> None:
        raise HTTPException(
            status_code=429,
            detail={"code": "WEBHOOK_RATE_LIMITED", "message": "limited"},
            headers={"Retry-After": "3"},
        )

    monkeypatch.setattr(webhooks_api, "_enforce_webhook_rate_limit", _limited)
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", AsyncMock())

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers=_headers("good-token"),
            json={"event": "demo"},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "WEBHOOK_RATE_LIMITED"
    webhooks_api.publish_cmd_with_ack_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_webhook_mock_ipc_reaches_subscribed_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDB(token="good-token", hook_key="orders")
    received: list[dict[str, Any]] = []

    class _WebhookPlugin(Plugin):
        key = "_test_webhook_receiver"
        display_name = "Webhook Receiver"

        async def on_event(self, ctx: PluginContext, payload: dict[str, Any]) -> list[dict[str, Any]]:
            received.append(payload)
            return []

    _WebhookPlugin._manifest = Manifest(
        key="_test_webhook_receiver",
        display_name="Webhook Receiver",
        event_subscriptions=[
            {
                "source": ["webhook"],
                "events": ["webhook"],
                "scope": "all_allowed_chats",
                "triggers": {"webhook": "orders"},
                "entry_key": "main",
            }
        ],
    )

    state = loader_mod._AccountState(1)
    state.redis = _FakeRedis()
    state.instances[_WebhookPlugin.key] = _WebhookPlugin()
    state.contexts[_WebhookPlugin.key] = PluginContext(
        account_id=1,
        feature_key=_WebhookPlugin.key,
        redis=state.redis,
        generation=state.generation,
    )
    loader_mod._STATES[1] = state

    async def _publish(redis: Any, account_id: int, cmd_type: str, **payload: Any) -> dict[str, Any] | None:
        assert redis is state.redis or isinstance(redis, _FakeRedis)
        assert account_id == 1
        assert cmd_type == CMD_WEBHOOK_DELIVER
        payload.pop("timeout", None)
        return await loader_mod.dispatch_webhook_event(account_id, payload, redis=state.redis)

    monkeypatch.setattr(webhooks_api, "get_redis", lambda: state.redis)
    monkeypatch.setattr(webhooks_api, "_enforce_webhook_rate_limit", AsyncMock(return_value={}))
    monkeypatch.setattr(webhooks_api, "publish_cmd_with_ack_payload", _publish)
    monkeypatch.setattr(
        loader_mod,
        "_load_event_framework_flags",
        AsyncMock(return_value={"trace_enabled": False, "event_bus_delivery_enabled": True}),
    )
    monkeypatch.setattr(loader_mod, "update_plugin_runtime_status", AsyncMock())

    try:
        async with _client(db) as client:
            response = await client.post(
                "/api/webhooks/1/orders",
                headers=_headers("good-token"),
                json={"order_id": "A-1", "status": "paid"},
            )
    finally:
        loader_mod._STATES.pop(1, None)

    assert response.status_code == 202
    assert response.json()["delivered"] is True
    assert len(received) == 1
    payload = received[0]
    assert payload["event_type"] == "webhook"
    assert payload["source"]["channel"] == "webhook"
    assert payload["trigger"]["hook_key"] == "orders"
    assert payload["webhook"]["body"] == {"order_id": "A-1", "status": "paid"}
    assert payload["webhook"]["headers"]["content-type"] == "application/json"
    assert "x-telepilot-webhook-token" not in payload["webhook"]["headers"]
    stored = db.settings[webhooks_api._setting_key(1)].value
    assert "token" not in stored
    assert decrypt_str(stored["token_enc"]) == "good-token"


@pytest.mark.asyncio
async def test_deliver_webhook_returns_trace_id_and_dispatch_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已受理响应必须带 trace_id 与命中统计，便于调用方按 trace 查处理结果。"""
    db = _FakeDB(token="good-token")
    ack = {
        "ok": True,
        "matched": 2,
        "invoked": 2,
        "failed": 0,
        "trace_id": "trace-webhook-1",
    }
    monkeypatch.setattr(
        webhooks_api, "publish_cmd_with_ack_payload", AsyncMock(return_value=ack)
    )
    monkeypatch.setattr(
        webhooks_api, "_enforce_webhook_rate_limit", AsyncMock(return_value={})
    )
    monkeypatch.setattr(webhooks_api, "_enforce_webhook_ingress_rate_limit", AsyncMock(return_value={}))

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers=_headers("good-token"),
            json={"event": "demo"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["delivered"] is True
    assert body["trace_id"] == "trace-webhook-1"
    assert body["matched"] == 2
    assert body["invoked"] == 2
    assert body["failed"] == 0


@pytest.mark.asyncio
async def test_deliver_webhook_logs_warning_when_dispatch_has_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """failed_count > 0 时必须留下 warning 日志，不能静默吞掉失败动作。"""
    db = _FakeDB(token="good-token")
    ack = {
        "ok": True,
        "matched": 1,
        "invoked": 1,
        "failed": 2,
        "trace_id": "trace-webhook-2",
    }
    monkeypatch.setattr(
        webhooks_api, "publish_cmd_with_ack_payload", AsyncMock(return_value=ack)
    )
    monkeypatch.setattr(
        webhooks_api, "_enforce_webhook_rate_limit", AsyncMock(return_value={})
    )
    monkeypatch.setattr(webhooks_api, "_enforce_webhook_ingress_rate_limit", AsyncMock(return_value={}))

    with caplog.at_level("WARNING", logger="app.api.webhooks"):
        async with _client(db) as client:
            response = await client.post(
                "/api/webhooks/1/default",
                headers=_headers("good-token"),
                json={"event": "demo"},
            )

    assert response.status_code == 202
    assert response.json()["failed"] == 2
    assert any(
        "trace-webhook-2" in record.message and "failed=2" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_deliver_webhook_503_when_worker_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 明确拒绝（ok=False）时仍返回 503，不把失败当成功。"""
    db = _FakeDB(token="good-token")
    monkeypatch.setattr(
        webhooks_api,
        "publish_cmd_with_ack_payload",
        AsyncMock(return_value={"ok": False, "error": "webhooks module disabled"}),
    )
    monkeypatch.setattr(
        webhooks_api, "_enforce_webhook_rate_limit", AsyncMock(return_value={})
    )
    monkeypatch.setattr(webhooks_api, "_enforce_webhook_ingress_rate_limit", AsyncMock(return_value={}))

    async with _client(db) as client:
        response = await client.post(
            "/api/webhooks/1/default",
            headers=_headers("good-token"),
            json={"event": "demo"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WEBHOOK_WORKER_OFFLINE"


def test_ipc_ack_payload_keeps_ok_false_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    """ACK 契约：publish_cmd_with_ack_payload 必须把 ok=False 的 ACK 也返回。"""
    import asyncio

    from app.worker import ipc

    class _PubSub:
        def __init__(self, cmd_id: str) -> None:
            self._cmd_id = cmd_id
            self._sent = False

        async def subscribe(self, _channel: str) -> None:
            return None

        async def get_message(self, **_kwargs: Any) -> dict[str, Any]:
            if self._sent:
                return {}
            self._sent = True
            return {
                "data": ipc.make_event(
                    ipc.EVT_ACK,
                    cmd_id=self._cmd_id,
                    cmd_type=CMD_WEBHOOK_DELIVER,
                    ok=False,
                    error="webhooks module disabled",
                )
            }

        async def unsubscribe(self, _channel: str) -> None:
            return None

        async def close(self) -> None:
            return None

    class _Redis:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []

        def pubsub(self) -> _PubSub:  # noqa: ANN202
            return _PubSub("fixed-cmd-id")

        async def publish(self, channel: str, message: str) -> int:
            self.published.append((channel, message))
            return 1

    monkeypatch.setattr(ipc.uuid, "uuid4", lambda: "fixed-cmd-id")
    redis = _Redis()
    ack = asyncio.run(
        ipc.publish_cmd_with_ack_payload(redis, 1, CMD_WEBHOOK_DELIVER, timeout=0.5)
    )

    assert ack is not None
    assert ack["ok"] is False
    assert ack["error"] == "webhooks module disabled"
    assert ack["cmd_type"] == CMD_WEBHOOK_DELIVER
