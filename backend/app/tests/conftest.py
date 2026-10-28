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
