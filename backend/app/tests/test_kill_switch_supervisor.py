"""紧急停用接口与 supervisor 的联动测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import rate_limit
from app.schemas.rate_limit import KillSwitchRequest
from app.worker import supervisor


@pytest.mark.asyncio
async def test_kill_switch_enabled_stops_running_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """开启紧急停用时，API 必须直接停止当前 supervisor 托管的 worker。"""
    set_setting = AsyncMock()
    audit = AsyncMock()
    stop_running_workers = AsyncMock()
    start_active_workers = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(rate_limit, "_set_setting", set_setting)
    monkeypatch.setattr(rate_limit, "_audit", audit)
    monkeypatch.setattr(supervisor, "stop_running_workers", stop_running_workers)
    monkeypatch.setattr(supervisor, "start_active_workers", start_active_workers)
    monkeypatch.setattr(rate_limit, "get_redis", lambda: SimpleNamespace(publish=publish))

    result = await rate_limit.post_kill_switch(
        KillSwitchRequest(enabled=True),
        db=AsyncMock(),
        user=SimpleNamespace(id=7),
    )

    assert result == {"enabled": True}
    set_setting.assert_awaited_once()
    audit.assert_awaited_once()
    stop_running_workers.assert_awaited_once()
    start_active_workers.assert_not_awaited()
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_disabled_starts_active_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """解除紧急停用时，API 必须恢复 DB 中 active 账号的 worker。"""
    set_setting = AsyncMock()
    audit = AsyncMock()
    stop_running_workers = AsyncMock()
    start_active_workers = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(rate_limit, "_set_setting", set_setting)
    monkeypatch.setattr(rate_limit, "_audit", audit)
    monkeypatch.setattr(supervisor, "stop_running_workers", stop_running_workers)
    monkeypatch.setattr(supervisor, "start_active_workers", start_active_workers)
    monkeypatch.setattr(rate_limit, "get_redis", lambda: SimpleNamespace(publish=publish))

    result = await rate_limit.post_kill_switch(
        KillSwitchRequest(enabled=False),
        db=AsyncMock(),
        user=SimpleNamespace(id=7),
    )

    assert result == {"enabled": False}
    set_setting.assert_awaited_once()
    audit.assert_awaited_once()
    stop_running_workers.assert_not_awaited()
    start_active_workers.assert_awaited_once()
    publish.assert_awaited_once()
