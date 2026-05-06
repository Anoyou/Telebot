"""账号绑定向导（``services/login_service.py``）的状态机单元测试。

不连真 Telethon、不连 DB：mock ``TelegramClient`` 验证状态机分支。
端到端 HTTP 测试需要 PG 方言（``ARRAY/BYTEA``），整合阶段再开。
"""

from __future__ import annotations

from datetime import UTC, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from app.services import login_service
from app.services.device_profile import HARDCODED_FALLBACK


# ── 公共 fixture ───────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clear_pending_table():
    """每个测试都从干净的 _PENDING / 锁状态开始。"""
    login_service._PENDING.clear()
    yield
    login_service._PENDING.clear()


@pytest.fixture(autouse=True)
def _stub_device_profile():
    """跳过 device_profile.get_default 的真实 DB 查询，直接返回 HARDCODED_FALLBACK。

    Sprint 2 #1 把 device_profile.get_default 接入 login_service.start_login 后，
    这些用 AsyncMock(db) 的测试会让 ``db.execute(...).scalar_one_or_none()`` 返回
    一个 truthy 的 AsyncMock，导致 ``_from_row(row)`` 取属性时拿到 coroutine。
    在测试环境直接 stub 掉，让登录状态机的测试聚焦在登录逻辑本身。

    login_service 用局部 import（``from .device_profile import get_default``），
    必须 patch 源模块本身。
    """
    with patch(
        "app.services.device_profile.get_default",
        AsyncMock(return_value=HARDCODED_FALLBACK),
    ), patch(
        "app.services.device_profile.get_by_id",
        AsyncMock(return_value=HARDCODED_FALLBACK),
    ):
        yield


def _make_fake_client(*, send_code_exc=None) -> AsyncMock:
    """构造一个 mock TelegramClient；可注入 send_code_request 抛出的异常。"""
    client = AsyncMock()
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    if send_code_exc is not None:
        client.send_code_request = AsyncMock(side_effect=send_code_exc)
    else:
        sent = AsyncMock()
        sent.phone_code_hash = "fake_hash_xyz"
        client.send_code_request = AsyncMock(return_value=sent)
    return client


# ── start_login ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_start_login_success_returns_token():
    """正常路径：发码成功，返回 login_token，并在 _PENDING 中可查到。"""
    fake_client = _make_fake_client()
    db = AsyncMock()
    # 不带 proxy_id，所以不会查 DB
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(
            db, api_id=1, api_hash="hash", phone="+8613800000000"
        )
    assert isinstance(token, str) and token
    pending = await login_service.get_pending(token)
    assert pending is not None
    assert pending.phone == "+8613800000000"
    assert pending.phone_code_hash == "fake_hash_xyz"
    fake_client.connect.assert_awaited_once()
    fake_client.send_code_request.assert_awaited_once_with("+8613800000000")


@pytest.mark.asyncio
async def test_start_login_phone_invalid_disconnects_and_raises():
    """PhoneNumberInvalidError 须回收 client 并向上抛 PHONE_INVALID。"""
    fake_client = _make_fake_client(send_code_exc=PhoneNumberInvalidError(request=None))
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        with pytest.raises(login_service.HTTPException) as exc_info:
            await login_service.start_login(db, api_id=1, api_hash="h", phone="bad")
    assert exc_info.value.detail["code"] == "PHONE_INVALID"
    fake_client.disconnect.assert_awaited()


# ── confirm_code ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_confirm_code_success_no_2fa():
    """验证码正确且账号未启用 2FA：返回 (False, pending)。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(return_value=None)
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    require_2fa, pending = await login_service.confirm_code(token, "12345")
    assert require_2fa is False
    assert pending.require_2fa is False
    fake_client.sign_in.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_code_2fa_required():
    """sign_in 抛 SessionPasswordNeededError → require_2fa=True 且 pending 仍在。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(side_effect=SessionPasswordNeededError(request=None))
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    require_2fa, pending = await login_service.confirm_code(token, "12345")
    assert require_2fa is True
    assert pending.require_2fa is True
    # pending 仍可查（等下一步 confirm_2fa）
    assert await login_service.get_pending(token) is pending


@pytest.mark.asyncio
async def test_confirm_code_invalid_keeps_pending():
    """PhoneCodeInvalidError → 抛 CODE_INVALID，pending 仍保留可重试。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(side_effect=PhoneCodeInvalidError(request=None))
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    with pytest.raises(login_service.HTTPException) as exc_info:
        await login_service.confirm_code(token, "wrong")
    assert exc_info.value.detail["code"] == "CODE_INVALID"
    # 仍可重试
    assert await login_service.get_pending(token) is not None


@pytest.mark.asyncio
async def test_confirm_code_invalid_exceeded_clears_pending_and_disconnects():
    """验证码连续错误超限：返回 LOGIN_ATTEMPTS_EXCEEDED，token 作废并回收 client。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(side_effect=PhoneCodeInvalidError(request=None))
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    for _ in range(4):
        with pytest.raises(login_service.HTTPException) as exc_info:
            await login_service.confirm_code(token, "wrong")
        assert exc_info.value.detail["code"] == "CODE_INVALID"
    with pytest.raises(login_service.HTTPException) as exc_info:
        await login_service.confirm_code(token, "wrong")
    assert exc_info.value.detail["code"] == "LOGIN_ATTEMPTS_EXCEEDED"
    assert await login_service.get_pending(token) is None
    fake_client.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_confirm_code_expired_clears_pending():
    """PhoneCodeExpiredError → 整个会话作废，pending 被清掉。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(side_effect=PhoneCodeExpiredError(request=None))
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    with pytest.raises(login_service.HTTPException) as exc_info:
        await login_service.confirm_code(token, "expired")
    assert exc_info.value.detail["code"] == "CODE_EXPIRED"
    assert await login_service.get_pending(token) is None


@pytest.mark.asyncio
async def test_confirm_code_unknown_token():
    """token 不存在（已过期或从未发起）→ LOGIN_TOKEN_EXPIRED。"""
    with pytest.raises(login_service.HTTPException) as exc_info:
        await login_service.confirm_code("no-such-token", "12345")
    assert exc_info.value.detail["code"] == "LOGIN_TOKEN_EXPIRED"


# ── confirm_2fa ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_confirm_2fa_success():
    """正确密码：sign_in 成功，返回 pending。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(return_value=None)
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    pending = await login_service.confirm_2fa(token, "my-2fa")
    assert pending is not None
    fake_client.sign_in.assert_awaited_with(password="my-2fa")


@pytest.mark.asyncio
async def test_confirm_2fa_invalid_password():
    """密码错 → PASSWORD_INVALID。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(side_effect=PasswordHashInvalidError(request=None))
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    with pytest.raises(login_service.HTTPException) as exc_info:
        await login_service.confirm_2fa(token, "wrong")
    assert exc_info.value.detail["code"] == "PASSWORD_INVALID"


@pytest.mark.asyncio
async def test_confirm_2fa_invalid_exceeded_clears_pending_and_disconnects():
    """2FA 密码连续错误超限：返回 LOGIN_ATTEMPTS_EXCEEDED，token 作废并回收 client。"""
    fake_client = _make_fake_client()
    fake_client.sign_in = AsyncMock(side_effect=PasswordHashInvalidError(request=None))
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    for _ in range(4):
        with pytest.raises(login_service.HTTPException) as exc_info:
            await login_service.confirm_2fa(token, "wrong")
        assert exc_info.value.detail["code"] == "PASSWORD_INVALID"
    with pytest.raises(login_service.HTTPException) as exc_info:
        await login_service.confirm_2fa(token, "wrong")
    assert exc_info.value.detail["code"] == "LOGIN_ATTEMPTS_EXCEEDED"
    assert await login_service.get_pending(token) is None
    fake_client.disconnect.assert_awaited()


# ── 后台 TTL 清理 ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pending_ttl_cleanup_logic():
    """直接验证 TTL 清理逻辑：把 created_at 调到 31 分钟前，再手动跑一轮清理。"""
    from datetime import datetime

    fake_client = _make_fake_client()
    fake_client.disconnect = AsyncMock()
    db = AsyncMock()
    with patch.object(login_service, "TelegramClient", return_value=fake_client):
        token = await login_service.start_login(db, api_id=1, api_hash="h", phone="+1")
    pending = login_service._PENDING[token]
    pending.created_at = datetime.now(UTC) - timedelta(minutes=31)

    # 复刻 cleanup_expired_loop 内的清理段
    now = datetime.now(UTC)
    expired = []
    async with login_service._LOCK:
        for tok, p in list(login_service._PENDING.items()):
            if now - p.created_at > login_service._PENDING_TTL:
                expired.append(p)
                login_service._PENDING.pop(tok, None)
    for p in expired:
        await login_service._safe_disconnect(p.client)

    assert token not in login_service._PENDING
    fake_client.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_start_login_rejects_when_pending_limit_exceeded(monkeypatch):
    """挂起登录达到上限时，start_login 应直接返回 429。"""
    monkeypatch.setattr(login_service.settings, "max_pending_logins", 1)
    login_service._PENDING["occupied"] = login_service._PendingLogin(
        client=AsyncMock(),
        api_id=1,
        api_hash="h",
        phone="+1",
    )
    with pytest.raises(login_service.HTTPException) as exc_info:
        await login_service.start_login(
            AsyncMock(),
            api_id=2,
            api_hash="h2",
            phone="+8613800000000",
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "LOGIN_PENDING_LIMITED"


# ── 端到端（占位，等整合阶段连真 PG 时启用） ──────────────────────
@pytest.mark.skip(reason="端到端 HTTP 测试需要 PG 方言；整合阶段补全 DB fixture")
async def test_login_wizard_e2e():
    """演示 /login/start → /login/code → finalize 全链路，整合阶段补全。"""
    pass
