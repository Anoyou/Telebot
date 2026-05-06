from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.api import auth as auth_api
from app.schemas.auth import TotpVerifyRequest


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, key: str):
        self.store.pop(key, None)


def _dummy_request() -> Request:
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/"})


@pytest.mark.asyncio
async def test_totp_enable_uses_redis_pending_state(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.api.auth.auth_service.generate_totp_secret", lambda: "ABCDEF123456")

    user = SimpleNamespace(id=7, username="admin")
    response = Response()
    out = await auth_api.totp_enable(user, response, _dummy_request())

    assert out.secret == "ABCDEF123456"
    assert fake_redis.store[f"{auth_api._PENDING_TOTP_REDIS_KEY_PREFIX}:7"] == "ABCDEF123456"
    set_cookie = response.headers.get("set-cookie", "")
    assert "pending_totp" in set_cookie
    assert "ABCDEF123456" not in set_cookie


@pytest.mark.asyncio
async def test_totp_verify_reads_redis_and_clears_pending(monkeypatch):
    fake_redis = _FakeRedis()
    fake_redis.store[f"{auth_api._PENDING_TOTP_REDIS_KEY_PREFIX}:9"] = "PENDINGSECRET"
    monkeypatch.setattr("app.redis_client.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.api.auth.auth_service.verify_totp", lambda _s, _c: True)
    monkeypatch.setattr("app.api.auth.encrypt_str", lambda s: f"enc:{s}")
    monkeypatch.setattr("app.api.auth.audit.write", AsyncMock(return_value=None))

    user = SimpleNamespace(id=9, username="root", totp_secret_enc=None)
    db = AsyncMock()
    db.add = AsyncMock(return_value=None)
    db.commit = AsyncMock(return_value=None)
    response = Response()

    out = await auth_api.totp_verify(
        TotpVerifyRequest(code="123456"),
        user,
        db,
        _dummy_request(),
        response,
    )

    assert out["ok"] is True
    assert user.totp_secret_enc == "enc:PENDINGSECRET"
    assert f"{auth_api._PENDING_TOTP_REDIS_KEY_PREFIX}:9" not in fake_redis.store
