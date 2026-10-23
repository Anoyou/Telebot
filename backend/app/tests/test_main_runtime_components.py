"""关键运行组件的启动自愈与 readiness 回归测试。"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main


@pytest.fixture(autouse=True)
def _restore_runtime_component_state():
    components = dict(main._RUNTIME_COMPONENTS)
    errors = dict(main._RUNTIME_COMPONENT_ERRORS)
    yield
    main._RUNTIME_COMPONENTS.clear()
    main._RUNTIME_COMPONENTS.update(components)
    main._RUNTIME_COMPONENT_ERRORS.clear()
    main._RUNTIME_COMPONENT_ERRORS.update(errors)


@pytest.mark.asyncio
async def test_start_runtime_component_records_failure_then_recovers() -> None:
    failing = AsyncMock(side_effect=RuntimeError("boom"))
    healthy = AsyncMock(return_value=None)

    assert await main._start_runtime_component("worker_supervisor", failing) is False
    assert main._RUNTIME_COMPONENTS["worker_supervisor"] is False
    assert "RuntimeError: boom" in main._RUNTIME_COMPONENT_ERRORS["worker_supervisor"]

    assert await main._start_runtime_component("worker_supervisor", healthy) is True
    assert main._RUNTIME_COMPONENTS["worker_supervisor"] is True
    assert "worker_supervisor" not in main._RUNTIME_COMPONENT_ERRORS


@pytest.mark.asyncio
async def test_retry_runtime_component_retries_until_success(monkeypatch) -> None:
    attempts = 0

    async def _starter() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")

    sleep = AsyncMock(return_value=None)
    monkeypatch.setattr(main.asyncio, "sleep", sleep)
    main._RUNTIME_COMPONENTS["account_bot_manager"] = False

    await main._retry_runtime_component("account_bot_manager", _starter)

    assert attempts == 2
    assert sleep.await_count == 2
    assert main._RUNTIME_COMPONENTS["account_bot_manager"] is True


@pytest.mark.asyncio
async def test_restore_system_agent_runs_eagerly_reconciles_durable_queue(
    monkeypatch,
) -> None:
    manager = AsyncMock()
    monkeypatch.setattr(main, "get_system_agent_run_manager", lambda: manager)

    restored = await main._restore_system_agent_runs()

    assert restored is manager
    manager.ensure_ready.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_restore_system_agent_runs_keeps_explicit_manager_on_failure() -> None:
    manager = AsyncMock()
    manager.ensure_ready.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await main._restore_system_agent_runs(manager)

    manager.ensure_ready.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_safe_watch_stops_interaction_manager_branch_and_marks_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        main.runtime_profile_service,
        "refresh_state_from_db",
        AsyncMock(return_value={"active_profile": "safe_watch", "status": "active"}),
    )
    start_manager = AsyncMock()
    mark_ready = AsyncMock()
    monkeypatch.setattr(
        main.interaction_bot_runtime, "start_interaction_bot_manager", start_manager
    )
    monkeypatch.setattr(
        main.platform_capabilities, "mark_runtime_ready_if_starting", mark_ready
    )

    result = await main._start_interaction_bot_component()

    assert result == 0
    start_manager.assert_not_awaited()
    mark_ready.assert_awaited_once_with("interaction_bot")


@pytest.mark.asyncio
async def test_production_profile_starts_interaction_manager(monkeypatch) -> None:
    monkeypatch.setattr(
        main.runtime_profile_service,
        "refresh_state_from_db",
        AsyncMock(return_value={"active_profile": None, "status": "idle"}),
    )
    monkeypatch.setattr(
        main.platform_capabilities,
        "get_snapshot",
        lambda: type("Snapshot", (), {"cache_ready": True})(),
    )
    monkeypatch.setattr(
        main.platform_capabilities, "is_module_enabled_cached", lambda *_args, **_kwargs: True
    )
    manager = object()
    start_manager = AsyncMock(return_value=manager)
    monkeypatch.setattr(
        main.interaction_bot_runtime, "start_interaction_bot_manager", start_manager
    )

    assert await main._start_interaction_bot_component() is manager
    start_manager.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_readyz_does_not_echo_dependency_error_text(monkeypatch, caplog) -> None:
    """/readyz 未认证可访问：依赖失败只返回组件名 + ok:false，不回显异常文本。"""

    marker = "readyz-secret-marker"

    class _FailingDBCtx:
        async def __aenter__(self):
            raise RuntimeError(f"db blew up with {marker}")

        async def __aexit__(self, *_exc):
            return False

    def _failing_session_local():
        return _FailingDBCtx()

    fake_redis = SimpleNamespace()
    fake_redis.ping = AsyncMock(side_effect=ConnectionError(f"redis {marker}"))

    import app.db.base as db_base
    import app.redis_client as redis_client

    monkeypatch.setattr(db_base, "AsyncSessionLocal", _failing_session_local)
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(
        main, "_RUNTIME_COMPONENT_ERRORS",
        {"worker_supervisor": f"RuntimeError: worker {marker}"},
    )
    monkeypatch.setattr(main, "_RUNTIME_COMPONENTS", {"worker_supervisor": False})

    with caplog.at_level(logging.WARNING):
        response = await main.readyz()

    # 503 + 结构化 checks：只有组件名和 ok 标记
    assert response.status_code == 503
    body = json.loads(response.body.decode())
    assert body["ok"] is False
    assert body["checks"]["db"] == {"ok": False}
    assert body["checks"]["redis"] == {"ok": False}
    assert body["checks"]["worker_supervisor"] == {"ok": False}
    # 异常文本 / 内部错误串不得进入响应
    assert marker not in response.body.decode()
    assert "RuntimeError" not in response.body.decode()
    # 异常细节仍在日志里，可排障
    assert marker in caplog.text


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_shared_bot_api_client(monkeypatch) -> None:
    """lifespan 退出序列必须在停止 account bot manager 后关闭共享 Bot API 客户端。"""

    order: list[str] = []

    async def _noop(*_args, **_kwargs):
        return None

    async def _sleep_forever():
        await asyncio.Event().wait()

    class _FakeManager:
        ensure_ready = AsyncMock()
        shutdown = AsyncMock(side_effect=lambda: order.append("run_manager.shutdown"))

    class _FakeGatewayManager:
        shutdown = AsyncMock(side_effect=lambda: order.append("gateway.shutdown"))

    monkeypatch.setattr(main, "_warn_if_forwarded_for_misconfigured", _noop)
    monkeypatch.setattr(main.settings, "auto_migrate_on_startup", False)
    monkeypatch.setattr(main.feature_service, "cleanup_removed_bundled_plugins", _noop)
    monkeypatch.setattr(main.event_trace, "refresh_trace_settings", _noop)
    monkeypatch.setattr(main.platform_capabilities, "bootstrap_from_db", _noop)
    monkeypatch.setattr(main.runtime_profile_service, "startup_restore", _noop)
    monkeypatch.setattr(main.runtime_profile_service, "shutdown", _noop)

    import app.services.gateway_runtime as gateway_runtime
    import app.services.llm_identity as llm_identity

    monkeypatch.setattr(llm_identity, "load_version_overrides_from_db", _noop)
    monkeypatch.setattr(gateway_runtime, "reconcile_gateway_runtime", _noop)
    monkeypatch.setattr(
        gateway_runtime, "gateway_runtime_manager", _FakeGatewayManager(), raising=False
    )
    monkeypatch.setattr(
        main.plugin_config_action_jobs, "startup_plugin_config_action_jobs", _noop
    )
    monkeypatch.setattr(
        main.plugin_config_action_jobs, "shutdown_plugin_config_action_jobs", _noop
    )
    monkeypatch.setattr(main, "get_system_agent_run_manager", lambda: _FakeManager())
    monkeypatch.setattr(main, "cleanup_expired_loop", _sleep_forever)
    monkeypatch.setattr(main, "cleanup_expired_action_secrets_loop", _sleep_forever)
    monkeypatch.setattr(main, "auto_update_check_loop", _sleep_forever)
    monkeypatch.setattr(main.remote_plugin_service, "auto_update_check_loop", _sleep_forever)
    monkeypatch.setattr(main.notify_service, "send", _noop)

    import app.worker.supervisor as supervisor

    monkeypatch.setattr(supervisor, "start_supervisor", _noop)
    monkeypatch.setattr(supervisor, "stop_all_workers", _noop)

    monkeypatch.setattr(
        main.account_bot_runtime,
        "start_account_bot_manager",
        AsyncMock(side_effect=lambda: order.append("account_bot.start")),
    )
    monkeypatch.setattr(
        main.account_bot_runtime,
        "stop_account_bot_manager",
        AsyncMock(side_effect=lambda: order.append("account_bot.stop")),
    )
    monkeypatch.setattr(main.interaction_bot_runtime, "stop_interaction_bot_manager", _noop)
    monkeypatch.setattr(main.platform_capabilities, "reconcile_runtime_after_startup", _noop)
    monkeypatch.setattr(main.event_trace, "stop_trace_writer", _noop)

    import app.services.account_bot_service as account_bot_service

    monkeypatch.setattr(
        account_bot_service,
        "close_bot_api_client",
        AsyncMock(side_effect=lambda: order.append("close_bot_api_client")),
    )

    app = SimpleNamespace()
    async with main.lifespan(app):
        pass

    assert "account_bot.stop" in order
    assert "close_bot_api_client" in order
    # 顺序：先停 manager，再关共享 HTTP 客户端
    assert order.index("account_bot.stop") < order.index("close_bot_api_client")
