"""Shared pytest compatibility helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if not hasattr(pytest, "mock"):
    pytest.mock = mock  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_local_userbot_rate_limit_state() -> None:
    """避免进程内本地限流桶在测试间泄漏（波次一 fail-closed 降级）。"""

    try:
        from app.worker import command as command_mod

        command_mod.reset_local_rate_limit_buckets()
        yield
        command_mod.reset_local_rate_limit_buckets()
    except Exception:  # noqa: BLE001
        yield


@pytest.fixture(autouse=True)
def _reset_bot_api_and_llm_reliability_state() -> None:
    """Bot API 限速桶与 LLM Provider 熔断器都是进程内状态，避免在测试间泄漏。"""

    from app.services import bot_api_limits, llm_reliability

    bot_api_limits.get_rate_limiter().reset()
    llm_reliability.get_circuit_breaker().reset()
    yield
    bot_api_limits.get_rate_limiter().reset()
    llm_reliability.get_circuit_breaker().reset()


@pytest.fixture(autouse=True)
def _isolate_bot_polling_runtime_state(monkeypatch):
    """r11：polling 调度器 Redis 持久化、交互配置缓存、插件状态待写表在用例间隔离。"""

    try:
        from app.services import account_bot_runtime as runtime_mod
        from app.services import event_trace as trace_mod
        from app.services import polling_dispatcher as dispatcher_mod
    except Exception:  # noqa: BLE001
        yield
        return
    monkeypatch.setattr(dispatcher_mod, "REDIS_PERSISTENCE_ENABLED", False)
    runtime_mod._INTERACTION_RUNTIME_CONFIG_CACHE.clear()
    trace_mod._PLUGIN_STATUS_PENDING.clear()
    trace_mod._PLUGIN_STATUS_TASK = None
    yield
    runtime_mod._INTERACTION_RUNTIME_CONFIG_CACHE.clear()
    runtime_mod._MANAGEMENT_AGENT_TASKS.clear()
    runtime_mod._MANAGEMENT_AGENT_LOCKS.clear()
    trace_mod._PLUGIN_STATUS_PENDING.clear()
    task = trace_mod._PLUGIN_STATUS_TASK
    if task is not None and not task.done():
        try:
            task.cancel()
        except Exception:  # noqa: BLE001
            pass
    trace_mod._PLUGIN_STATUS_TASK = None


@pytest.fixture(autouse=True)
def _isolate_payment_sources():
    """B1：payment_sources 发现缓存是进程内状态；默认视为“没有插件来源”且不查库，用例按需注入。"""

    try:
        from app.services import payment_sources
    except Exception:  # noqa: BLE001
        yield
        return
    payment_sources.reset_cache()
    payment_sources.set_sources_for_test([])
    payment_sources.set_authoritative_hint(False)
    yield
    payment_sources.reset_cache()
    payment_sources.set_authoritative_hint(False)
