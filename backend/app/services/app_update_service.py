"""应用自更新检查服务（只检查，不自动应用）。

把原本堆在 ``api/system_health.py`` 的更新检查核心逻辑收敛到这里，
供 ``POST /api/system/check-update`` 路由壳、TopBar 角标的
``GET /api/system/update-status`` 与 lifespan 后台循环共用同一份实现。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from ..db.base import AsyncSessionLocal
from ..db.models.system import SystemSetting

log = logging.getLogger(__name__)

SETTING_CHECK = "app_update_check"
SETTING_LAST_CHECK = "app_update_last_check"

DEFAULT_INTERVAL_MINUTES = 1440
MIN_INTERVAL_MINUTES = 60
MAX_INTERVAL_MINUTES = 10080

def normalize_check_setting(value: Any) -> dict[str, Any]:
    """把 DB 里的设置收敛成 ``{enabled, interval_minutes}``；非法值回落默认。"""
    if not isinstance(value, dict):
        value = {}
    enabled = bool(value.get("enabled", True))
    return {
        "enabled": enabled,
        "interval_minutes": clamp_interval_minutes(value.get("interval_minutes")),
    }


def clamp_interval_minutes(value: Any) -> int:
    """收敛到 ``60..10080`` 分钟；非正数 / 不可解析视为未设置，回落默认值。"""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES
    if minutes <= 0:
        return DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, minutes))


async def load_check_setting(db: Any) -> dict[str, Any]:
    row = await db.get(SystemSetting, SETTING_CHECK)
    return normalize_check_setting(row.value if row is not None else None)


async def save_check_setting(
    db: Any, *, enabled: bool | None, interval_minutes: int | None
) -> dict[str, Any]:
    """读现值 → merge → 落库；任一字段缺省时保留原值。"""
    current = await load_check_setting(db)
    merged = {
        "enabled": current["enabled"] if enabled is None else bool(enabled),
        "interval_minutes": (
            current["interval_minutes"]
            if interval_minutes is None
            else clamp_interval_minutes(interval_minutes)
        ),
    }
    row = await db.get(SystemSetting, SETTING_CHECK)
    if row is None:
        db.add(SystemSetting(key=SETTING_CHECK, value=merged))
    else:
        row.value = merged
    await db.flush()
    return merged


async def load_last_check(db: Any) -> dict[str, Any] | None:
    row = await db.get(SystemSetting, SETTING_LAST_CHECK)
    if row is None:
        return None
    return row.value if isinstance(row.value, dict) else None


async def save_last_check(db: Any, result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    payload["checked_at"] = datetime.now(UTC).isoformat()
    row = await db.get(SystemSetting, SETTING_LAST_CHECK)
    if row is None:
        db.add(SystemSetting(key=SETTING_LAST_CHECK, value=payload))
    else:
        row.value = payload
    await db.flush()
    return payload


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    """只保留角标需要的最小事实集，避免把完整 plan 落库。"""
    return {
        "has_update": bool(result.get("has_update")),
        "can_check": bool(result.get("can_check", True)),
        "current_version": result.get("current_version"),
        "target_version": result.get("target_version"),
        "current_commit": result.get("current_commit"),
        "remote_commit": result.get("remote_commit"),
        "remote": result.get("remote"),
        "branch": result.get("branch"),
        "error": result.get("error"),
    }


def _same_commit(left: Any, right: Any) -> bool:
    """短 commit 与长 commit 按前缀比较；任一侧缺失或过短时视为无法判断（False）。"""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    a = left.strip().lower()
    b = right.strip().lower()
    if len(a) < 7 or len(b) < 7:
        return False
    return a.startswith(b) or b.startswith(a)


def is_last_check_stale(last: dict[str, Any] | None, running_commit: str | None) -> bool:
    """判断"有新版本"的旧结论是否已被部署变更推翻。

    - 取不到运行提交，或旧结论本身没有 ``has_update``、没有记录 ``current_commit``
      （老版本写入的摘要）时不做判断；
    - 运行提交已等于当时的目标 ``remote_commit``：已更新到目标，结论陈旧；
    - 记录的 ``current_commit`` 与运行提交不一致：检查之后服务器已换过版本，
      结论陈旧。例外是 updater 报告"Git 已前进、容器尚未切换"的待部署状态
      （此时 ``current_commit`` 与 ``remote_commit`` 相同），这时运行提交本就
      落后于 ``current_commit``，不能据此判陈旧。
    """
    if not last or not last.get("has_update") or not running_commit:
        return False
    current_commit = last.get("current_commit")
    remote_commit = last.get("remote_commit")
    if _same_commit(running_commit, remote_commit):
        return True
    if not isinstance(current_commit, str) or len(current_commit.strip()) < 7:
        return False
    if _same_commit(current_commit, remote_commit):
        return False
    return not _same_commit(current_commit, running_commit)


def _runtime_commit_sync() -> str | None:
    """与 ``GET /api/system/version`` 的 ``revision``（部署提交）同源。"""
    from ..api.system_health import _runtime_revision

    return _runtime_revision()


async def current_runtime_commit() -> str | None:
    """读取服务器当前运行提交；任何异常都视为取不到（不做陈旧判断）。"""
    try:
        return await asyncio.to_thread(_runtime_commit_sync)
    except Exception:  # noqa: BLE001
        log.debug("读取当前运行提交失败", exc_info=True)
        return None


async def resolve_last_check(last: dict[str, Any] | None) -> dict[str, Any] | None:
    """返回给角标前处理陈旧结论：陈旧时 ``has_update`` 置 False 并标记 ``stale``。"""
    if not last or not last.get("has_update"):
        return last
    if not last.get("current_commit") and not last.get("remote_commit"):
        # 老版本写入的摘要没有 commit 字段，无从判断，原样返回。
        return last
    running = await current_runtime_commit()
    if not is_last_check_stale(last, running):
        return last
    resolved = dict(last)
    resolved["has_update"] = False
    resolved["stale"] = True
    resolved["running_commit"] = running[:12] if running else None
    return resolved


async def run_check(check_call: Any) -> dict[str, Any]:
    """执行一次更新检查并把结果摘要写回设置。

    ``check_call`` 是 ``api/system_health`` 暴露的检查实现，延迟传入避免
    服务层反向依赖 API 层（也避免循环 import）。
    """
    payload = await check_call()
    try:
        async with AsyncSessionLocal() as db:
            await save_last_check(db, summarize_result(payload))
            await db.commit()
    except Exception:  # noqa: BLE001
        log.warning("写入应用更新检查结果失败", exc_info=True)
    return payload


async def load_update_status() -> dict[str, Any]:
    """TopBar 角标读取的一次性状态快照。"""
    try:
        async with AsyncSessionLocal() as db:
            check = await load_check_setting(db)
            last = await load_last_check(db)
    except Exception:  # noqa: BLE001
        log.warning("读取应用更新检查状态失败", exc_info=True)
        return {
            "enabled": True,
            "interval_minutes": DEFAULT_INTERVAL_MINUTES,
            "last_check": None,
        }
    return {
        "enabled": check["enabled"],
        "interval_minutes": check["interval_minutes"],
        "last_check": await resolve_last_check(last),
    }


async def default_check_call() -> dict[str, Any]:
    """默认检查实现：调用 system_health 里的检查核心。"""
    from ..api.system_health import run_update_check_for_scheduler

    return await run_update_check_for_scheduler()


# 后台循环启动等待与轮询粒度：设置变更最多一分钟后生效。
STARTUP_DELAY_SECONDS = 15
LOOP_TICK_SECONDS = 60


async def _sleep(seconds: float) -> None:
    """后台循环唯一的等待入口，测试可替换以压缩时间。"""
    await asyncio.sleep(seconds)


def _conclusion(last: dict[str, Any] | None) -> tuple[Any, ...] | None:
    """把"是否有新版本"收敛成可比较的结论；无更新时返回 None。"""
    if not last or not last.get("has_update"):
        return None
    return (
        last.get("current_version"),
        last.get("target_version"),
        last.get("remote"),
        last.get("branch"),
        last.get("error"),
    )


async def _check_and_log(state: dict[str, Any], check_call: Any) -> None:
    """执行一次检查，只在"发现新版本且结论发生变化"时留一条日志。

    ``state`` 由调用方持有，用来跨轮记住上一条已记录的结论；
    绝不在这里触发拉取或重启。
    """
    await run_check(check_call)
    last = (await load_update_status()).get("last_check")
    conclusion = _conclusion(last)
    if conclusion is None or conclusion == state.get("last_conclusion"):
        return
    state["last_conclusion"] = conclusion
    log.info(
        "检测到应用新版本 current=%s target=%s remote=%s/%s error=%s",
        last.get("current_version"),
        last.get("target_version"),
        last.get("remote"),
        last.get("branch"),
        last.get("error"),
    )


async def auto_update_check_loop() -> None:
    """后台自动检查应用更新。

    只负责"检查 + 记录 + 发现新版本时落一条日志"，绝不自动 pull / restart。
    """
    await _sleep(STARTUP_DELAY_SECONDS)
    state: dict[str, Any] = {}
    last_run_at: datetime | None = None
    while True:
        try:
            status = await load_update_status()
            now = datetime.now(UTC)
            due = last_run_at is None or (
                now - last_run_at
            ).total_seconds() >= status["interval_minutes"] * 60
            if status["enabled"] and due:
                await _check_and_log(state, default_check_call)
                last_run_at = now
        except Exception:  # noqa: BLE001
            last_run_at = datetime.now(UTC)
            log.warning("应用自动检查更新失败", exc_info=True)
        await _sleep(LOOP_TICK_SECONDS)


__all__ = [
    "DEFAULT_INTERVAL_MINUTES",
    "LOOP_TICK_SECONDS",
    "MAX_INTERVAL_MINUTES",
    "MIN_INTERVAL_MINUTES",
    "SETTING_CHECK",
    "SETTING_LAST_CHECK",
    "STARTUP_DELAY_SECONDS",
    "_check_and_log",
    "_sleep",
    "auto_update_check_loop",
    "clamp_interval_minutes",
    "current_runtime_commit",
    "default_check_call",
    "is_last_check_stale",
    "load_check_setting",
    "load_last_check",
    "load_update_status",
    "normalize_check_setting",
    "resolve_last_check",
    "run_check",
    "save_check_setting",
    "save_last_check",
    "summarize_result",
]
