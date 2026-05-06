from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker.plugins.builtin.scheduler.plugin import SchedulerPlugin, _parse_dt


class _Decision:
    allowed = True
    outcome = "ok"
    wait_seconds = 0


@pytest.mark.asyncio
async def test_interval_first_tick_fires_and_sets_next_fire() -> None:
    plugin = SchedulerPlugin()
    now = datetime.now(UTC)
    cfg = {
        "kind": "interval",
        "interval_sec": 60,
        "action": {"type": "send_message", "target_chat_id": 123, "text": "tick"},
    }
    rule = SimpleNamespace(id=1, config=cfg)

    ctx = SimpleNamespace(
        account_id=7,
        rules=[rule],
        client=SimpleNamespace(send_message=AsyncMock()),
        engine=SimpleNamespace(acquire=AsyncMock(return_value=_Decision()), on_flood_wait=AsyncMock()),
        log=AsyncMock(),
    )

    plugin._persist_rule_config = AsyncMock()  # type: ignore[method-assign]
    await plugin._tick_once(ctx)

    ctx.client.send_message.assert_awaited_once_with(123, "tick")
    saved_cfg = plugin._persist_rule_config.await_args_list[-1].args[1]  # type: ignore[attr-defined]
    assert saved_cfg["last_result"] == "ok"
    assert _parse_dt(saved_cfg["next_fire"]) is not None
    assert _parse_dt(saved_cfg["next_fire"]) > now


@pytest.mark.asyncio
async def test_once_rule_disables_after_fire() -> None:
    plugin = SchedulerPlugin()
    fire_at = (datetime.now(UTC) - timedelta(seconds=3)).isoformat()
    cfg = {
        "kind": "once",
        "fire_at": fire_at,
        "action": {"type": "send_message", "target_chat_id": 321, "text": "hello"},
    }
    rule = SimpleNamespace(id=2, config=cfg)

    ctx = SimpleNamespace(
        account_id=9,
        rules=[rule],
        client=SimpleNamespace(send_message=AsyncMock()),
        engine=SimpleNamespace(acquire=AsyncMock(return_value=_Decision()), on_flood_wait=AsyncMock()),
        log=AsyncMock(),
    )

    plugin._persist_rule_config = AsyncMock()  # type: ignore[method-assign]
    await plugin._tick_once(ctx)

    saved_cfg = plugin._persist_rule_config.await_args_list[-1].args[1]  # type: ignore[attr-defined]
    assert saved_cfg["enabled"] is False
    assert saved_cfg["next_fire"] is None
    assert saved_cfg["last_result"] == "ok"


@pytest.mark.asyncio
async def test_cron_sets_next_fire_when_missing() -> None:
    plugin = SchedulerPlugin()
    cfg = {
        "kind": "cron",
        "cron": "*/1 * * * *",
        "action": {"type": "send_message", "target_chat_id": 1, "text": "noop"},
    }
    rule = SimpleNamespace(id=3, config=cfg)

    ctx = SimpleNamespace(
        account_id=1,
        rules=[rule],
        client=SimpleNamespace(send_message=AsyncMock()),
        engine=SimpleNamespace(acquire=AsyncMock(return_value=_Decision()), on_flood_wait=AsyncMock()),
        log=AsyncMock(),
    )

    plugin._persist_rule_config = AsyncMock()  # type: ignore[method-assign]
    await plugin._tick_once(ctx)

    # 第一次只写 next_fire，不会立即 fire
    ctx.client.send_message.assert_not_awaited()
    saved_cfg = plugin._persist_rule_config.await_args_list[-1].args[1]  # type: ignore[attr-defined]
    assert _parse_dt(saved_cfg["next_fire"]) is not None


def test_parse_dt_accepts_z_suffix() -> None:
    dt = _parse_dt("2026-05-10T10:11:12Z")
    assert dt is not None
    assert dt.tzinfo is not None
