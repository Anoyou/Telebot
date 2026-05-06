"""account_service 的单元测试（不连真 DB/Redis）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.account import AccountUpdateRequest
from app.services import account_service


@pytest.mark.asyncio
async def test_update_account_proxy_changed_triggers_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """当 proxy_id/template_id 发生变化时，必须触发 worker 重启。"""
    acc = SimpleNamespace(id=1, proxy_id=10, template_id=20, display_name="old")
    db = AsyncMock()
    db.get = AsyncMock(return_value=acc)
    db.commit = AsyncMock(return_value=None)

    published: list[tuple[str, str]] = []

    async def _fake_publish(channel: str, payload: str) -> None:
        published.append((channel, payload))

    monkeypatch.setattr(account_service, "_publish", _fake_publish)
    monkeypatch.setattr(
        account_service,
        "get_account",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )

    await account_service.update_account(
        db,
        1,
        AccountUpdateRequest(proxy_id=11, template_id=20, display_name="new"),
    )

    assert acc.proxy_id == 11
    assert len(published) == 2
    channel0, payload0 = published[0]
    channel1, payload1 = published[1]
    assert channel0 == account_service.cmd_channel(1)
    assert '"type":"stop"' in payload0
    assert channel1 == account_service.GLOBAL_CHANNEL
    assert '"type":"start_worker"' in payload1
    assert '"account_id":1' in payload1


@pytest.mark.asyncio
async def test_update_account_non_runtime_fields_no_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """仅改 display_name/notes/tags 等非运行时字段时，不应通知 reload。"""
    acc = SimpleNamespace(id=2, proxy_id=10, template_id=20, display_name="old")
    db = AsyncMock()
    db.get = AsyncMock(return_value=acc)
    db.commit = AsyncMock(return_value=None)

    publish_mock = AsyncMock()
    monkeypatch.setattr(account_service, "_publish", publish_mock)
    monkeypatch.setattr(
        account_service,
        "get_account",
        AsyncMock(return_value=SimpleNamespace(id=2)),
    )

    await account_service.update_account(
        db,
        2,
        AccountUpdateRequest(display_name="new-name", notes="n"),
    )

    publish_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_account_not_found() -> None:
    """账号不存在应返回 ACCOUNT_NOT_FOUND。"""
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(account_service.HTTPException) as exc_info:
        await account_service.update_account(db, 404, AccountUpdateRequest(display_name="x"))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "ACCOUNT_NOT_FOUND"
