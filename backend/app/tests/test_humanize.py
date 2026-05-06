"""拟人化纯函数测试。"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import pytest

from app.worker.ratelimit.humanize import (
    HumanizeOpts,
    cold_start_factor,
    in_active_window,
    jitter,
    seconds_until_active_window,
)


# ─────────────────────────────────────────────────────
# jitter
# ─────────────────────────────────────────────────────
def test_jitter_zero_pct_returns_base() -> None:
    """pct=0 时直接返回基线，不引入随机。"""
    assert jitter(1.0, 0) == 1.0
    assert jitter(0.0, 0) == 0.0


def test_jitter_clamped_to_zero() -> None:
    """随机抖动可能为负，结果必须 clamp 到 0。"""
    random.seed(0)
    for _ in range(100):
        assert jitter(0.01, 99) >= 0


def test_jitter_within_range() -> None:
    """pct=20% 的抖动结果应该在 [base*0.8, base*1.2] 之间。"""
    random.seed(42)
    base = 10.0
    for _ in range(200):
        v = jitter(base, 20)
        assert base * 0.8 - 1e-9 <= v <= base * 1.2 + 1e-9


# ─────────────────────────────────────────────────────
# in_active_window
# ─────────────────────────────────────────────────────
def test_in_active_window_no_config_always_true() -> None:
    opts = HumanizeOpts()
    assert in_active_window(opts, datetime(2026, 5, 2, 3, 0, 0))


def test_in_active_window_normal() -> None:
    """常规白天窗口 09:00 - 21:00。"""
    opts = HumanizeOpts(active_window_start=time(9, 0), active_window_end=time(21, 0))
    assert in_active_window(opts, datetime(2026, 5, 2, 12, 0))
    assert not in_active_window(opts, datetime(2026, 5, 2, 8, 0))
    assert not in_active_window(opts, datetime(2026, 5, 2, 22, 0))


def test_in_active_window_overnight() -> None:
    """跨夜窗口 22:00 - 06:00。"""
    opts = HumanizeOpts(active_window_start=time(22, 0), active_window_end=time(6, 0))
    assert in_active_window(opts, datetime(2026, 5, 2, 23, 0))  # 晚上
    assert in_active_window(opts, datetime(2026, 5, 2, 3, 0))  # 凌晨
    assert not in_active_window(opts, datetime(2026, 5, 2, 12, 0))  # 中午


def test_in_active_window_equal_means_24h() -> None:
    """起止相同 → 视作全天可用，避免出现死区。"""
    opts = HumanizeOpts(active_window_start=time(0, 0), active_window_end=time(0, 0))
    assert in_active_window(opts, datetime(2026, 5, 2, 12, 0))


# ─────────────────────────────────────────────────────
# cold_start_factor
# ─────────────────────────────────────────────────────
def test_cold_start_no_config() -> None:
    assert cold_start_factor(HumanizeOpts()) == 1.0


def test_cold_start_already_passed() -> None:
    opts = HumanizeOpts(cold_start_until=date(2026, 5, 1), cold_start_days=7)
    assert cold_start_factor(opts, today=date(2026, 5, 2)) == 1.0


def test_cold_start_first_day_lowest() -> None:
    """刚开始冷启动时 days_left=days_total，progress=0，factor=0.3。"""
    opts = HumanizeOpts(cold_start_until=date(2026, 5, 9), cold_start_days=7)
    f = cold_start_factor(opts, today=date(2026, 5, 2))
    assert 0.299 < f < 0.301


def test_cold_start_progress_monotonic() -> None:
    """随天数推进系数单调上升。"""
    opts = HumanizeOpts(cold_start_until=date(2026, 5, 9), cold_start_days=7)
    prev = -1.0
    for offset in range(8):
        f = cold_start_factor(opts, today=date(2026, 5, 2) + timedelta(days=offset))
        assert f >= prev
        prev = f
    assert prev == 1.0  # 第 8 天达终点


def test_cold_start_clamped() -> None:
    """异常配置（days_total=0）走兜底，结果仍在 [0.3, 1.0]。"""
    opts = HumanizeOpts(cold_start_until=date(2026, 5, 9), cold_start_days=0)
    f = cold_start_factor(opts, today=date(2026, 5, 2))
    assert 0.3 <= f <= 1.0


# ─────────────────────────────────────────────────────
# seconds_until_active_window
# ─────────────────────────────────────────────────────
def test_seconds_until_default_60() -> None:
    """未设活跃时段 → 兜底 60s。"""
    assert seconds_until_active_window(HumanizeOpts()) == 60.0


def test_seconds_until_today_future() -> None:
    """目标时间在今天稍后：返回相差秒数。"""
    opts = HumanizeOpts(active_window_start=time(15, 0), active_window_end=time(20, 0))
    now = datetime(2026, 5, 2, 14, 0, 0)
    assert seconds_until_active_window(opts, now=now) == 3600.0


def test_seconds_until_tomorrow() -> None:
    """已过今天目标：返回明天目标差。"""
    opts = HumanizeOpts(active_window_start=time(9, 0), active_window_end=time(20, 0))
    now = datetime(2026, 5, 2, 21, 0, 0)
    expected = (24 - 21 + 9) * 3600.0
    assert seconds_until_active_window(opts, now=now) == pytest.approx(expected)
