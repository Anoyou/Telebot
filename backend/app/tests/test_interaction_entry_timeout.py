"""交互入口 manifest ``timeout_seconds``：默认 15 秒，可声明放宽，平台上限 90 秒。"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services import account_bot_service
from app.services.account_bot_runtime import _interaction_entry_timeout_seconds
from app.worker.plugins import loader


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (45, 45.0),
        ("30", 30.0),
        (300, 90.0),
        (0.2, 1.0),
    ],
)
def test_normalize_entry_keeps_clamped_timeout(raw, expected) -> None:
    entry = account_bot_service.normalize_interaction_entry_manifest({"key": "chat", "timeout_seconds": raw})
    assert entry is not None
    assert entry["timeout_seconds"] == expected


@pytest.mark.parametrize("raw", [None, 0, -5, "abc", True, [], float("nan")])
def test_normalize_entry_drops_invalid_timeout(raw) -> None:
    entry = account_bot_service.normalize_interaction_entry_manifest({"key": "chat", "timeout_seconds": raw})
    assert entry is not None
    assert "timeout_seconds" not in entry


def test_declared_timeout_defaults_to_15_seconds(monkeypatch) -> None:
    monkeypatch.setattr(account_bot_service, "declared_module_entry_manifest", lambda *_a: None)
    assert account_bot_service.declared_module_entry_timeout_seconds("x", "y") == 15.0
    assert _interaction_entry_timeout_seconds("x", "y") == 15.0


def test_declared_timeout_reads_manifest(monkeypatch) -> None:
    monkeypatch.setattr(
        account_bot_service,
        "declared_module_entry_manifest",
        lambda *_a: {"key": "chat", "timeout_seconds": 45},
    )
    assert account_bot_service.declared_module_entry_timeout_seconds("interaction-ai-chat", "chat") == 45.0
    assert _interaction_entry_timeout_seconds("interaction-ai-chat", "chat") == 45.0


def test_runtime_timeout_falls_back_when_lookup_fails(monkeypatch) -> None:
    def boom(*_a):
        raise RuntimeError("manifest broken")

    monkeypatch.setattr(account_bot_service, "declared_module_entry_timeout_seconds", boom)
    assert _interaction_entry_timeout_seconds("x", "y") == 15.0


async def test_interaction_deadline_can_exceed_global_invoke_timeout(monkeypatch) -> None:
    """交互入口以主进程下发的 deadline 为准，不再被全局 plugin_invoke_timeout_seconds 截断。"""

    monkeypatch.setattr(loader.app_settings, "plugin_invoke_timeout_seconds", 0.05)
    monkeypatch.setattr(loader, "plugin_update_in_progress", lambda *_a: False)
    state = loader._AccountState(1)
    deadline_at_ms = int((time.time() + 5) * 1000)

    async def slow() -> str:
        await asyncio.sleep(0.2)
        return "done"

    result = await loader._invoke_plugin_with_resilience(
        state, "interaction-ai-chat", slow, deadline_at_ms=deadline_at_ms, deadline_governs=True
    )
    assert result == "done"

    # 其它调用方保持原语义：取全局时限与 deadline 的较小值。
    with pytest.raises(TimeoutError):
        await loader._invoke_plugin_with_resilience(state, "other", slow, deadline_at_ms=deadline_at_ms)


def test_event_subscription_timeout_prefers_subscription_declaration(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services.account_bot_runtime import _event_subscription_timeout_seconds

    monkeypatch.setattr(account_bot_service, "declared_module_entry_manifest", lambda *_a: None)
    declared = SimpleNamespace(
        plugin_key="interaction-ai-chat",
        entry_key=None,
        subscription=SimpleNamespace(raw={"events": ["message"], "timeout_seconds": 45}),
    )
    assert _event_subscription_timeout_seconds(declared) == 45.0
    capped = SimpleNamespace(plugin_key="p", entry_key=None, subscription=SimpleNamespace(raw={"timeout_seconds": 600}))
    assert _event_subscription_timeout_seconds(capped) == 90.0
    plain = SimpleNamespace(plugin_key="p", entry_key=None, subscription=SimpleNamespace(raw={}))
    assert _event_subscription_timeout_seconds(plain) == 15.0


def test_chat_not_allowed_drop_logs_info(caplog) -> None:
    import logging
    from types import SimpleNamespace

    from app.services.account_bot_runtime import _log_event_bus_chat_not_allowed

    incoming = SimpleNamespace(account_id=7, chat_id=-100123)
    dropped = SimpleNamespace(
        matched=False, reason_code="scope_not_matched", scope="all_allowed_chats", plugin_key="interaction-ai-chat"
    )
    with caplog.at_level(logging.INFO, logger="app.services.account_bot_runtime"):
        _log_event_bus_chat_not_allowed(incoming, dropped, {"allowed_chat_ids": [-100999]})
        # 允许全部会话、或其它原因被跳过时不记录。
        _log_event_bus_chat_not_allowed(incoming, dropped, {"allowed_chat_ids": "*"})
        other = SimpleNamespace(matched=False, reason_code="event_not_matched", scope="all_allowed_chats", plugin_key="x")
        _log_event_bus_chat_not_allowed(incoming, other, {"allowed_chat_ids": [-100999]})
    records = [r for r in caplog.records if "允许会话" in r.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "account=7" in message and "chat_id=-100123" in message and "interaction-ai-chat" in message
