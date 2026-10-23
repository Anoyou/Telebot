from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import plugins as plugins_api


def test_legacy_plugin_entry_rejects_non_builtin_with_scoped_message(monkeypatch) -> None:
    monkeypatch.setattr(plugins_api, "_is_builtin", lambda _plugin_key: False)

    with pytest.raises(HTTPException) as exc_info:
        plugins_api._ensure_builtin_or_501("demo")

    assert exc_info.value.status_code == 501
    assert exc_info.value.detail == {
        "code": "NOT_IMPLEMENTED",
        "message": "该旧入口仅支持内置插件: demo",
    }


@pytest.mark.asyncio
async def test_uninstall_plugin_passes_triggered_by_user_id(monkeypatch) -> None:
    """卸载与 install/enable 同口径：bulk_set_enabled 必须带上 triggered_by_user_id。"""
    bulk_set_enabled = AsyncMock(return_value=2)
    audit_write = AsyncMock()
    monkeypatch.setattr(plugins_api, "_is_builtin", lambda _plugin_key: True)
    monkeypatch.setattr(plugins_api, "_ensure_accounts_exist", AsyncMock())
    monkeypatch.setattr(plugins_api.feature_service, "bulk_set_enabled", bulk_set_enabled)
    monkeypatch.setattr(plugins_api.audit, "write", audit_write)
    db = AsyncMock()

    payload = plugins_api.PluginInstallRequest(plugin_key="demo", account_ids=[1, 2])
    out = await plugins_api.uninstall_plugin(payload, db, SimpleNamespace(id=9))

    assert out == {"applied": 2}
    bulk_set_enabled.assert_awaited_once_with(
        db, [1, 2], "demo", enabled=False, triggered_by_user_id=9
    )
    audit_write.assert_awaited_once()
    assert audit_write.await_args.args[2] == "plugin.uninstall"
