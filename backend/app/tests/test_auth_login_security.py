from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.api import auth as auth_api
from app.db.models.system import SystemSetting
from app.schemas.auth import LoginRequest
from app.services import auth_login_security


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _FakeDB:
    def __init__(self, execute_values=None):
        self.execute_values = list(execute_values or [])
        self.settings: dict[str, SystemSetting] = {}
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        return self.execute_values.pop(0)

    async def get(self, model, key):
        if model is SystemSetting:
            return self.settings.get(key)
        return None

    async def scalar(self, _stmt):
        return self.settings.get(auth_login_security.RECOVERY_SETTING_KEY)

    def add(self, row):
        self.added.append(row)
        if isinstance(row, SystemSetting):
            self.settings[row.key] = row

    async def commit(self):
        self.committed = True


def _request(ip: str = "127.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"user-agent", b"pytest")],
            "method": "POST",
            "path": "/api/auth/login",
            "client": (ip, 12345),
        }
    )


@pytest.fixture(autouse=True)
def _clear_auth_security_state():
    auth_login_security._LOCAL_FAILS.clear()
    auth_login_security._LOCAL_CHALLENGES.clear()
    yield
    auth_login_security._LOCAL_FAILS.clear()
    auth_login_security._LOCAL_CHALLENGES.clear()


@pytest.mark.asyncio
async def test_recovery_code_is_hashed_and_single_use():
    db = _FakeDB()
    user = SimpleNamespace(id=1, username="admin")

    code, _expires_at = await auth_login_security.create_recovery_code(db, user=user, ttl_seconds=300)
    stored = db.settings[auth_login_security.RECOVERY_SETTING_KEY].value

    assert code
    assert code not in json.dumps(stored, ensure_ascii=False)
    assert await auth_login_security.verify_recovery_code(db, user=user, code=code) is True
    assert await auth_login_security.verify_recovery_code(db, user=user, code=code) is False


@pytest.mark.asyncio
async def test_notify_otp_challenge_uses_notify_bot_and_is_one_time(monkeypatch):
    monkeypatch.setattr(auth_login_security, "_get_redis", lambda: (_ for _ in ()).throw(RuntimeError("no redis")))
    monkeypatch.setattr(auth_login_security, "_make_otp_code", lambda: "123456")
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(auth_login_security.notify_service, "send", send)
    user = SimpleNamespace(id=7, username="root")

    challenge = await auth_login_security.issue_login_otp_challenge(
        user=user,
        ip="10.0.0.1",
        user_agent="pytest",
    )

    assert challenge.sent is True
    assert challenge.token
    assert send.await_count == 1
    assert await auth_login_security.verify_login_otp(token=challenge.token, code="123456", user=user) is True
    assert await auth_login_security.verify_login_otp(token=challenge.token, code="123456", user=user) is False


@pytest.mark.asyncio
async def test_wrong_password_records_failure_without_otp(monkeypatch):
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc=None)
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)])

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: False)
    record_failure = AsyncMock(return_value=None)
    issue_challenge = AsyncMock()
    monkeypatch.setattr(auth_api.auth_login_security, "record_login_failure", record_failure)
    monkeypatch.setattr(auth_api.auth_login_security, "issue_login_otp_challenge", issue_challenge)

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.login(
            LoginRequest(username="admin", password="bad"),
            _request(),
            Response(),
            db,
        )

    assert exc_info.value.status_code == 401
    record_failure.assert_awaited_once()
    issue_challenge.assert_not_awaited()


@pytest.mark.asyncio
async def test_correct_password_after_failures_requires_notify_otp(monkeypatch):
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc=None)
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)])

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: True)
    monkeypatch.setattr(auth_api.auth_login_security, "should_require_login_otp", AsyncMock(return_value=True))
    monkeypatch.setattr(
        auth_api.auth_login_security,
        "issue_login_otp_challenge",
        AsyncMock(
            return_value=auth_login_security.LoginOtpChallenge(
                token="challenge-token",
                sent=True,
                delivery="notify_bot",
                message="已发送",
                ttl_seconds=300,
            )
        ),
    )

    out = await auth_api.login(
        LoginRequest(username="admin", password="ok"),
        _request(),
        Response(),
        db,
    )

    assert out.ok is False
    assert out.require_otp is True
    assert out.otp_token == "challenge-token"
    assert out.otp_delivery == "notify_bot"


@pytest.mark.asyncio
async def test_recovery_code_bypasses_totp_and_otp_after_password(monkeypatch):
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc="enc:totp")
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)])
    response = Response()

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: True)
    monkeypatch.setattr(auth_api.auth_login_security, "verify_recovery_code", AsyncMock(return_value=True))
    monkeypatch.setattr(auth_api.auth_login_security, "clear_login_failures", AsyncMock(return_value=None))
    should_otp = AsyncMock(return_value=True)
    monkeypatch.setattr(auth_api.auth_login_security, "should_require_login_otp", should_otp)
    monkeypatch.setattr(auth_api.audit, "write", AsyncMock(return_value=None))

    out = await auth_api.login(
        LoginRequest(username="admin", password="ok", recovery_code="TP-XXXXX"),
        _request(),
        response,
        db,
    )

    assert out.ok is True
    assert out.require_totp is False
    assert out.require_otp is False
    should_otp.assert_not_awaited()
    assert "auth_token=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_totp_secret_does_not_require_code_when_login_security_totp_off(monkeypatch):
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc="enc:totp")
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)])
    response = Response()

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: True)
    monkeypatch.setattr(auth_api.auth_login_security, "clear_login_failures", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.audit, "write", AsyncMock(return_value=None))

    out = await auth_api.login(
        LoginRequest(username="admin", password="ok"),
        _request(),
        response,
        db,
    )

    assert out.ok is True
    assert out.require_totp is False
    assert "auth_token=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_totp_secret_requires_code_when_login_security_totp_on(monkeypatch):
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc="enc:totp")
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)])
    db.settings["login_security"] = SystemSetting(
        key="login_security",
        value={"totp_enabled": True, "totp_mode": "always"},
    )

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: True)

    out = await auth_api.login(
        LoginRequest(username="admin", password="ok"),
        _request(),
        Response(),
        db,
    )

    assert out.ok is False
    assert out.require_totp is True


@pytest.mark.asyncio
async def test_totp_after_failures_does_not_require_code_before_threshold(monkeypatch):
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc="enc:totp")
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)])
    db.settings["login_security"] = SystemSetting(
        key="login_security",
        value={
            "totp_enabled": True,
            "totp_mode": "after_failures",
            "totp_failed_attempt_threshold": 2,
        },
    )
    response = Response()

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: True)
    monkeypatch.setattr(auth_api.auth_login_security, "clear_login_failures", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.audit, "write", AsyncMock(return_value=None))
    monkeypatch.setattr(
        auth_api.auth_login_security,
        "_get_redis",
        lambda: (_ for _ in ()).throw(RuntimeError("no redis")),
    )

    out = await auth_api.login(
        LoginRequest(username="admin", password="ok"),
        _request(),
        response,
        db,
    )

    assert out.ok is True
    assert out.require_totp is False
    assert "auth_token=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_totp_after_failures_tracks_failures_without_notify_otp(monkeypatch):
    monkeypatch.setattr(
        auth_login_security,
        "_get_redis",
        lambda: (_ for _ in ()).throw(RuntimeError("no redis")),
    )
    config = auth_login_security.normalize_login_security_config(
        {
            "notify_otp_enabled": False,
            "totp_enabled": True,
            "totp_mode": "after_failures",
            "totp_failed_attempt_threshold": 2,
            "notify_otp_fail_window_seconds": 900,
        }
    )

    await auth_login_security.record_login_failure("127.0.0.1", "admin", config=config)
    assert await auth_login_security.should_require_totp("127.0.0.1", "admin", config=config) is False

    await auth_login_security.record_login_failure("127.0.0.1", "admin", config=config)
    assert await auth_login_security.should_require_totp("127.0.0.1", "admin", config=config) is True


def test_legacy_totp_enabled_defaults_to_always_mode():
    config = auth_login_security.normalize_login_security_config({"totp_enabled": True})

    assert config.totp_enabled is True
    assert config.totp_mode == auth_login_security.TOTP_MODE_ALWAYS


class _AtomicOnlyRedis:
    """只支持 eval 的假 Redis：出现裸 INCR / EXPIRE 调用即失败，用于锁死原子路径。"""

    def __init__(self):
        self.buckets: dict[str, tuple[int, int]] = {}  # key -> (count, ttl)

    async def eval(self, _script: str, numkeys: int, key: str, ttl: int):
        assert numkeys == 1
        count, cur_ttl = self.buckets.get(key, (0, int(ttl)))
        self.buckets[key] = (count + 1, cur_ttl)
        return count + 1

    async def incr(self, *_args, **_kwargs):  # pragma: no cover - 非原子路径禁止再用
        raise AssertionError("counter must use atomic eval, not bare INCR")

    async def expire(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("counter must use atomic eval, not bare EXPIRE")


@pytest.mark.asyncio
async def test_record_login_failure_uses_atomic_incr_expire(monkeypatch):
    """失败计数必须走原子 INCR+EXPIRE：EXPIRE 丢失会让失败 key 永不过期、用户被永久递进锁定。"""
    fake = _AtomicOnlyRedis()
    monkeypatch.setattr(auth_login_security, "_get_redis", lambda: fake)
    config = auth_login_security.normalize_login_security_config(
        {
            "notify_otp_enabled": True,
            "notify_otp_failed_attempt_threshold": 3,
            "notify_otp_fail_window_seconds": 900,
        }
    )

    await auth_login_security.record_login_failure("10.0.0.1", "admin", config=config)
    await auth_login_security.record_login_failure("10.0.0.1", "admin", config=config)

    # ip 与 user 两个 key 各计 2 次；TTL 与首次设置时保持一致（固定窗口语义不变）
    assert sorted(v[0] for v in fake.buckets.values()) == [2, 2]
    assert {v[1] for v in fake.buckets.values()} == {900}


@pytest.mark.asyncio
async def test_totp_wrong_code_records_login_failure(monkeypatch):
    """TOTP 猜错必须与密码错误同口径计入失败记录，否则可长期慢速爆破 6 位码。"""
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc="enc:totp")
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)])
    db.settings["login_security"] = SystemSetting(
        key="login_security",
        value={"totp_enabled": True, "totp_mode": "always"},
    )

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: True)
    monkeypatch.setattr(auth_api, "decrypt_str", lambda _s: "TOTPSECRET")
    monkeypatch.setattr(auth_api.auth_service, "verify_totp_step", lambda _s, _c: None)
    record_failure = AsyncMock(return_value=None)
    monkeypatch.setattr(auth_api.auth_login_security, "record_login_failure", record_failure)

    with pytest.raises(HTTPException) as exc_info:
        await auth_api.login(
            LoginRequest(username="admin", password="ok", totp_code="000000"),
            _request(),
            Response(),
            db,
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "TOTP_INVALID"
    record_failure.assert_awaited_once()
    assert record_failure.await_args.args == ("127.0.0.1", "admin")


@pytest.mark.asyncio
async def test_totp_login_same_step_twice_both_succeed(monkeypatch):
    """同一 30s 窗口内用同一枚正确验证码连续两次登录都必须成功。

    消费式防重放曾把同窗口内的第二次正常登录（换设备/刷新重登/双端同时登录）
    连同正确密码一起拒绝——正确凭据永远不能被拒；防爆破由"TOTP 错误计入失败
    计数 + 限速 + 递进锁定"承担。
    """
    user = SimpleNamespace(id=1, username="admin", password_hash="hash", totp_secret_enc="enc:totp")
    db = _FakeDB([_ScalarResult(1), _ScalarResult(user)] * 2)
    db.settings["login_security"] = SystemSetting(
        key="login_security",
        value={"totp_enabled": True, "totp_mode": "always"},
    )

    monkeypatch.setattr(auth_api, "_enforce_login_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.auth_service, "verify_password_with_sentinel", lambda *_args: True)
    monkeypatch.setattr(auth_api, "decrypt_str", lambda _s: "TOTPSECRET")
    monkeypatch.setattr(auth_api.auth_login_security, "clear_login_failures", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_api.audit, "write", AsyncMock(return_value=None))
    record_failure = AsyncMock(return_value=None)
    monkeypatch.setattr(auth_api.auth_login_security, "record_login_failure", record_failure)
    monkeypatch.setattr(auth_api.auth_service, "verify_totp_step", lambda _s, _c: 59759297)

    # 同一 step（同一枚验证码）连续两次登录都成功。
    first = await auth_api.login(
        LoginRequest(username="admin", password="ok", totp_code="123456"),
        _request(),
        Response(),
        db,
    )
    assert first.ok is True
    second = await auth_api.login(
        LoginRequest(username="admin", password="ok", totp_code="123456"),
        _request(),
        Response(),
        db,
    )
    assert second.ok is True
    record_failure.assert_not_awaited()
