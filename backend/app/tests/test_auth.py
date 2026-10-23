"""认证服务的单元测试。

不依赖 DB：直接覆盖 ``services/auth_service.py`` 的纯函数。
端到端 API 测试需要 PG 方言（``ARRAY/BYTEA``），等整合阶段再开。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request
from starlette.responses import Response

from app.api import auth as auth_api
from app.schemas.auth import RegisterRequest
from app.services import auth_service


# ── 密码 ──────────────────────────────────────────────────────────
def test_hash_and_verify_password_roundtrip():
    """正确密码可校验通过，错误密码必须被拒绝。"""
    h = auth_service.hash_password("S3cret!!")
    assert h.startswith("$argon2")
    assert auth_service.verify_password("S3cret!!", h) is True
    assert auth_service.verify_password("wrong", h) is False


def test_verify_password_with_invalid_hash():
    """伪造的哈希字符串不应当抛异常，而是返回 False。"""
    assert auth_service.verify_password("any", "not-a-valid-hash") is False


def test_verify_password_with_sentinel_for_missing_user():
    """用户不存在时也执行等价校验，但结果必须为 False。"""
    assert auth_service.verify_password_with_sentinel("any", None) is False


# ── JWT ───────────────────────────────────────────────────────────
def test_issue_and_decode_jwt():
    """颁发的 token 能被解码出原 user_id。"""
    token = auth_service.issue_jwt_token(42, 3)
    assert isinstance(token, str) and token
    assert auth_service.decode_jwt_token(token) == 42
    claims = auth_service.decode_jwt_claims(token)
    assert claims is not None
    assert claims["pwd_v"] == 3


def test_decode_invalid_jwt_returns_none():
    """坏 token 一律返回 None，不抛异常。"""
    assert auth_service.decode_jwt_token("not.a.token") is None
    assert auth_service.decode_jwt_token("") is None


def test_decode_expired_jwt_returns_none(monkeypatch):
    """过期 token 应当返回 None。"""
    # 把 jwt_expire_seconds 临时改为负数，立刻过期
    from app.settings import settings

    monkeypatch.setattr(settings, "jwt_expire_seconds", -10)
    token = auth_service.issue_jwt_token(7)
    # 给一个微小延时确保 exp < now
    time.sleep(0.01)
    assert auth_service.decode_jwt_token(token) is None


# ── TOTP ──────────────────────────────────────────────────────────
def test_totp_secret_and_verify():
    """同一 secret 生成的 6 位码必须验证通过；错误码被拒。"""
    import pyotp

    secret = auth_service.generate_totp_secret()
    assert isinstance(secret, str) and len(secret) >= 16

    code = pyotp.TOTP(secret).now()
    assert auth_service.verify_totp(secret, code) is True
    assert auth_service.verify_totp(secret, "000000") is False
    assert auth_service.verify_totp("", code) is False
    assert auth_service.verify_totp(secret, "") is False


def test_make_otpauth_url_format():
    """otpauth url 应包含 issuer / username / secret。"""
    url = auth_service.make_otpauth_url("admin", "ABC234")
    assert url.startswith("otpauth://totp/")
    assert "secret=ABC234" in url
    assert "issuer=" in url
    assert "admin" in url


def test_verify_totp_step_returns_matched_step():
    """verify_totp_step 返回命中的 timestep：±1 窗口内的码返回对应 step，错误码为 None。"""
    import pyotp

    secret = auth_service.generate_totp_secret()
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    base = now // 30

    assert auth_service.verify_totp_step(secret, totp.at(now)) == base
    assert auth_service.verify_totp_step(secret, totp.at(now, -1)) == base - 1
    assert auth_service.verify_totp_step(secret, totp.at(now, 1)) == base + 1
    assert auth_service.verify_totp_step(secret, "000000") is None
    assert auth_service.verify_totp_step("", totp.at(now)) is None
    assert auth_service.verify_totp_step(secret, "") is None


@pytest.mark.asyncio
async def test_register_integrity_race_returns_register_disabled(monkeypatch) -> None:
    """两个首次注册同时通过 COUNT 时，数据库唯一约束失败应返回稳定的 403。"""

    db = AsyncMock()
    db.add = Mock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: 0))
    db.flush = AsyncMock(side_effect=IntegrityError("insert", {}, RuntimeError("unique")))
    enforce = AsyncMock()
    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", enforce)
    monkeypatch.setattr(auth_api.auth_service, "hash_password", lambda _password: "hash")
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    with pytest.raises(auth_api.HTTPException) as exc_info:
        await auth_api.register(
            RegisterRequest(username="admin", password="S3cret!!"),
            request,
            Response(),
            db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "REGISTER_DISABLED"
    db.rollback.assert_awaited_once()


def test_register_request_rejects_weak_password():
    """注册密码与改密同口径：少于 8 位直接被 schema 拒绝（API 层表现为 422）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RegisterRequest(username="admin", password="short")  # 6 位
    ok = RegisterRequest(username="admin", password="S3cret!!")
    assert ok.password == "S3cret!!"


@pytest.mark.asyncio
async def test_login_rate_limit_counter_is_atomic_incr_expire(monkeypatch):
    """限速计数必须走原子 INCR+EXPIRE（Lua eval）。

    裸 INCR 后补 EXPIRE 的非原子路径一旦在两条命令之间崩溃/断连，key 会失去
    TTL 且只增不减，最终对应 IP/用户名被永久 429，只能人工清 Redis。
    """
    eval_calls: list[tuple[str, int]] = []

    class _AtomicOnlyRedis:
        async def eval(self, _script: str, numkeys: int, key: str, ttl: int):
            assert numkeys == 1
            eval_calls.append((key, int(ttl)))
            return 1

        async def incr(self, *_args, **_kwargs):  # pragma: no cover - 旧路径禁止再用
            raise AssertionError("rate-limit must use atomic eval, not bare INCR")

        async def expire(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("rate-limit must use atomic eval, not bare EXPIRE")

    monkeypatch.setattr("app.redis_client.get_redis", lambda: _AtomicOnlyRedis())
    from app.settings import settings

    monkeypatch.setattr(settings, "login_rate_limit_per_min", 5)
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    await auth_api._enforce_login_rate_limit(request, "admin")

    # IP 与归一化用户名两个 key 都必须一次 eval 原子完成，TTL 固定 60s
    assert len(eval_calls) == 2
    assert {ttl for _key, ttl in eval_calls} == {60}
    assert {key for key, _ttl in eval_calls} == {"auth_rl:ip:127.0.0.1", "auth_rl:user:admin"}


# ── 端到端（占位，等整合阶段连真 PG 时启用） ──────────────────────
@pytest.mark.skip(reason="端到端 API 测试需要 PG 方言（ARRAY/BYTEA），整合阶段再开")
async def test_register_and_login_e2e():
    """演示 register → login → /me 的端到端流程，整合阶段补全 DB fixture。"""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/register",
            json={"username": "admin", "password": "S3cret!!"},
        )
        assert r.status_code == 200
        assert r.cookies.get("auth_token")

        r = await c.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "admin"
        assert body["has_totp"] is False
