"""拟人化：抖动 / 打字模拟 / 阅读延迟 / 活跃时段 / 冷启动渐进。

设计原则：所有函数都是 **纯函数 / 可测**，副作用（实际 sleep、调 ``client.action``）
集中在 ``simulate_typing`` / ``simulate_read`` 两个协程里。engine 在 ``acquire`` 返回
``wait_seconds`` 之前调 ``jitter()`` 给基线加上随机抖动。

Telethon 依赖只在 type-checking 时引入，避免本模块被 API 层引入时强制拉 telethon。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型提示用，不在运行时强引入 telethon
    from telethon import TelegramClient


@dataclass
class HumanizeOpts:
    """拟人化运行时配置。

    与 ``HumanizeConfig`` ORM 字段一一对应；engine 由 service 层传入。
    """

    jitter_pct: int = 15
    typing_simulate: bool = True
    typing_min_ms: int = 1000
    typing_max_ms: int = 3000
    typing_probability: int = 80
    read_before_reply: bool = True
    active_window_start: time | None = None
    active_window_end: time | None = None
    cold_start_days: int = 7
    cold_start_until: date | None = None


def jitter(base_seconds: float, pct: int) -> float:
    """对基线秒数加 ±pct% 的均匀抖动。

    pct<=0 时直接返回基线；负值结果会 clamp 到 0，避免反向 sleep。
    """
    if pct <= 0:
        return max(0.0, float(base_seconds))
    delta = float(base_seconds) * pct / 100.0
    return max(0.0, float(base_seconds) + random.uniform(-delta, delta))


def in_active_window(opts: HumanizeOpts, now: datetime | None = None) -> bool:
    """是否在主动动作允许的活跃时段内。

    规则：
      - 起止任一未配置 → 视为始终允许（True）
      - start <= end：常规白天窗口
      - start > end：跨夜窗口，例如 22:00–06:00
    """
    if opts.active_window_start is None or opts.active_window_end is None:
        return True
    cur = (now or datetime.now()).time()
    s, e = opts.active_window_start, opts.active_window_end
    if s == e:
        # 起止相同：等于"24h 全开"，避免出现死区
        return True
    if s <= e:
        return s <= cur <= e
    # 跨夜：在 start 之后或 end 之前都算允许
    return cur >= s or cur <= e


def cold_start_factor(opts: HumanizeOpts, today: date | None = None) -> float:
    """冷启动渐进系数：返回 0.3..1.0 之间的浮点数。

    设计：
      - 未设 ``cold_start_until`` → 1.0（完全放开）
      - today >= cold_start_until → 1.0（冷启动期已过）
      - 否则随剩余天数线性插值：剩余越多系数越低（越保守）
        progress = 1 - days_left/days_total
        factor = 0.3 + 0.7 * progress
    """
    if opts.cold_start_until is None:
        return 1.0
    today = today or date.today()
    if today >= opts.cold_start_until:
        return 1.0
    days_left = (opts.cold_start_until - today).days
    days_total = max(1, opts.cold_start_days or 7)
    progress = max(0.0, 1.0 - days_left / days_total)
    factor = 0.3 + 0.7 * progress
    # clamp 到 [0.3, 1.0] 防御
    return min(1.0, max(0.3, factor))


def seconds_until_active_window(opts: HumanizeOpts, now: datetime | None = None) -> float:
    """距离下一个活跃时段开始的秒数（粗略）。

    用于 policy=queue 在"不在活跃时段"时给出合理的 wait_seconds。
    """
    if opts.active_window_start is None:
        return 60.0
    from datetime import timedelta as _td

    now = now or datetime.now()
    target_today = datetime.combine(now.date(), opts.active_window_start)
    if target_today > now:
        return (target_today - now).total_seconds()
    return (target_today + _td(days=1) - now).total_seconds()


# ─────────────────────────────────────────────────────
# 副作用部分：真正调用 Telethon（异常一律吞掉，不让拟人化挡住业务）
# ─────────────────────────────────────────────────────
async def simulate_typing(client: TelegramClient, peer: Any, opts: HumanizeOpts) -> None:
    """按概率发送 ``typing`` action 一段随机时长，模拟人在打字。

    任何异常都吞掉：拟人化失败不应影响业务发送。
    """
    if not opts.typing_simulate:
        return
    if opts.typing_probability <= 0:
        return
    if random.randint(1, 100) > opts.typing_probability:
        return
    lo = max(0, int(opts.typing_min_ms))
    hi = max(lo, int(opts.typing_max_ms))
    duration_ms = random.randint(lo, hi) if hi > lo else lo
    try:
        async with client.action(peer, "typing"):
            await asyncio.sleep(duration_ms / 1000)
    except Exception:
        # 拟人化是 best-effort，吞掉异常即可
        pass


async def simulate_read(client: TelegramClient, peer: Any, opts: HumanizeOpts) -> None:
    """自动回复前先模拟"已读"：随机延迟 0.5~2s 后调 send_read_acknowledge。"""
    if not opts.read_before_reply:
        return
    delay = random.uniform(0.5, 2.0)
    await asyncio.sleep(delay)
    try:
        await client.send_read_acknowledge(peer)
    except Exception:
        pass
