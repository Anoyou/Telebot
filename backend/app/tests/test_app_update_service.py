"""应用自动检查更新：设置 clamp、落库摘要与后台循环行为。"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.system import SystemSetting
from app.services import app_update_service


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SystemSetting.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def test_default_setting_is_daily() -> None:
    assert app_update_service.normalize_check_setting(None) == {
        "enabled": True,
        "interval_minutes": 1440,
    }
    assert app_update_service.normalize_check_setting("garbage") == {
        "enabled": True,
        "interval_minutes": 1440,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (1, 60),  # 下限 1 小时
        (60, 60),
        (360, 360),
        (1440, 1440),
        (99999, 10080),  # 上限 7 天
        (0, 1440),  # 非正数视为未设置
        (-5, 1440),
        ("abc", 1440),
        (None, 1440),
    ),
)
def test_interval_clamp(raw, expected) -> None:
    assert app_update_service.clamp_interval_minutes(raw) == expected


@pytest.mark.asyncio
async def test_save_check_setting_merges_missing_fields(db_factory) -> None:
    async with db_factory() as db:
        assert await app_update_service.save_check_setting(
            db, enabled=None, interval_minutes=360
        ) == {"enabled": True, "interval_minutes": 360}
        # 只给 enabled：interval 保留现值（半量 update，不是整体覆盖）
        assert await app_update_service.save_check_setting(
            db, enabled=False, interval_minutes=None
        ) == {"enabled": False, "interval_minutes": 360}
        await db.commit()
        row = await db.get(SystemSetting, app_update_service.SETTING_CHECK)
        assert row is not None and row.value == {"enabled": False, "interval_minutes": 360}


@pytest.mark.asyncio
async def test_check_setting_survives_reopen(db_factory) -> None:
    """落库后再开一个会话读回，merge 结果必须可序列化往返。"""
    async with db_factory() as db:
        await app_update_service.save_check_setting(db, enabled=True, interval_minutes=90)
        await db.commit()

    async with db_factory() as db:
        assert await app_update_service.load_check_setting(db) == {
            "enabled": True,
            "interval_minutes": 90,
        }


@pytest.mark.asyncio
async def test_save_last_check_stamps_time_and_keeps_summary(db_factory) -> None:
    async with db_factory() as db:
        payload = await app_update_service.save_last_check(
            db,
            app_update_service.summarize_result(
                {
                    "has_update": True,
                    "can_check": True,
                    "current_version": "0.100.0-beta.6",
                    "target_version": "0.100.0-beta.7",
                    "remote": "origin",
                    "branch": "Beta",
                    # 完整 plan 里的大字段不应落库
                    "changed_files": ["a" * 10] * 50,
                    "commit_titles": ["x"] * 30,
                }
            ),
        )
        await db.commit()

        stored = await app_update_service.load_last_check(db)
        assert stored is not None
        assert stored["has_update"] is True
        assert stored["target_version"] == "0.100.0-beta.7"
        assert stored["branch"] == "Beta"
        assert stored["checked_at"]
        assert "changed_files" not in stored
        assert "commit_titles" not in stored
        assert payload["checked_at"]


@pytest.mark.asyncio
async def test_run_check_persists_only_summary(db_factory, monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_check():
        calls["n"] += 1
        return {
            "has_update": False,
            "can_check": True,
            "current_version": "1.2.3",
            "target_version": None,
            "remote": "origin",
            "branch": "main",
            "error": None,
            "changed_files": ["big"] * 100,
        }

    monkeypatch.setattr(app_update_service, "AsyncSessionLocal", db_factory)
    payload = await app_update_service.run_check(fake_check)
    assert calls["n"] == 1
    assert payload["has_update"] is False

    async with db_factory() as db:
        stored = await app_update_service.load_last_check(db)
        assert stored is not None and stored["current_version"] == "1.2.3"
        assert "changed_files" not in stored


@pytest.mark.asyncio
async def test_run_check_never_raises_when_db_write_fails(monkeypatch) -> None:
    """落库失败不能让后台循环把"已检查"当成完成。"""

    class _Broken:
        def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *_exc) -> bool:
            return False

    async def fake_check():
        return {"has_update": False, "can_check": True, "current_version": "1.2.3"}

    monkeypatch.setattr(app_update_service, "AsyncSessionLocal", lambda: _Broken())
    result = await app_update_service.run_check(fake_check)
    assert result["current_version"] == "1.2.3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answers", "expected_logs"),
    (
        # 无更新：一次都不记录
        ([{"has_update": False, "current_version": "1.0.0"}], 0),
        # 连续两次相同结论：只记录一次
        (
            [
                {"has_update": True, "current_version": "1.0.0", "target_version": "1.0.1"},
                {"has_update": True, "current_version": "1.0.0", "target_version": "1.0.1"},
            ],
            1,
        ),
        # 升级到新目标版本：再记录一次
        (
            [
                {"has_update": True, "current_version": "1.0.0", "target_version": "1.0.1"},
                {"has_update": True, "current_version": "1.0.0", "target_version": "1.0.2"},
            ],
            2,
        ),
        # 从有更新回到无更新：不记录
        (
            [
                {"has_update": True, "current_version": "1.0.0", "target_version": "1.0.1"},
                {"has_update": False, "current_version": "1.0.0"},
            ],
            1,
        ),
    ),
)
async def test_check_and_log_only_records_changed_conclusion(
    db_factory, monkeypatch, caplog, answers, expected_logs
) -> None:
    checks = {"n": 0}

    async def fake_check():
        result = answers[min(checks["n"], len(answers) - 1)]
        checks["n"] += 1
        return result

    monkeypatch.setattr(app_update_service, "AsyncSessionLocal", db_factory)

    with caplog.at_level(logging.INFO, logger="app.services.app_update_service"):
        state: dict = {}
        for _ in range(len(answers)):
            await app_update_service._check_and_log(state, fake_check)

    assert checks["n"] == len(answers)
    records = [r for r in caplog.records if "检测到应用新版本" in r.message]
    assert len(records) == expected_logs


@pytest.mark.asyncio
async def test_auto_loop_skips_check_when_disabled(monkeypatch) -> None:
    """enabled=False：后台循环不发起任何检查。"""
    checks = {"n": 0}

    async def fake_check():
        checks["n"] += 1
        return {"has_update": False, "current_version": "1.0.0"}

    async def fake_status():
        return {"enabled": False, "interval_minutes": 60, "last_check": None}

    async def immediate(_seconds: float | None = None) -> None:
        # 暴露 yield 点让后台循环推进，测试自己的等待不受影响。
        await _loop_yield()

    monkeypatch.setattr(app_update_service, "default_check_call", fake_check)
    monkeypatch.setattr(app_update_service, "load_update_status", fake_status)
    monkeypatch.setattr(app_update_service, "_sleep", immediate)

    task = asyncio.create_task(app_update_service.auto_update_check_loop())
    for _ in range(100):
        await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert checks["n"] == 0


@pytest.mark.asyncio
async def test_auto_loop_never_pulls_or_restarts(monkeypatch) -> None:
    """发现新版本也只记录，绝不触发 pull / restart 相关调用。"""
    pulled: list[str] = []

    async def fake_check():
        return {
            "has_update": True,
            "can_check": True,
            "current_version": "1.0.0",
            "target_version": "1.0.1",
        }

    async def fake_status():
        return {"enabled": True, "interval_minutes": 60, "last_check": None}

    async def immediate(_seconds: float | None = None) -> None:
        # 暴露 yield 点让后台循环推进，测试自己的等待不受影响。
        await _loop_yield()

    monkeypatch.setattr(app_update_service, "default_check_call", fake_check)
    monkeypatch.setattr(app_update_service, "load_update_status", fake_status)
    monkeypatch.setattr(app_update_service, "_check_and_log", _spy_noop(pulled))
    monkeypatch.setattr(app_update_service, "_sleep", immediate)

    task = asyncio.create_task(app_update_service.auto_update_check_loop())
    for _ in range(100):
        await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(pulled) >= 1  # 循环确实跑到了检查分支
    # 服务模块没有任何 pull / restart / deploy 入口可供调用
    assert not any(
        name in app_update_service.__all__ for name in ("pull", "restart", "deploy", "apply")
    )


async def _loop_yield() -> None:
    """后台循环时间压缩：只让出一次事件循环控制权。"""
    await asyncio.sleep(0)


def _spy_noop(sink: list[str]):
    async def _inner(_state, check_call):
        sink.append("check")
        return None

    return _inner


@pytest.mark.asyncio
async def test_load_update_status_degrades_gracefully_on_db_error(monkeypatch) -> None:
    class _Broken:
        def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(app_update_service, "AsyncSessionLocal", lambda: _Broken())
    status = await app_update_service.load_update_status()
    assert status["last_check"] is None
    assert status["interval_minutes"] == app_update_service.DEFAULT_INTERVAL_MINUTES
    assert datetime.now(UTC).tzinfo is not None


# ── 更新后旧结论陈旧判定 ─────────────────────────────────────────


def test_summarize_result_keeps_commits() -> None:
    summary = app_update_service.summarize_result(
        {
            "has_update": True,
            "current_commit": "aaaa1111aaaa",
            "remote_commit": "bbbb2222bbbb",
            "changed_files": ["x"],
        }
    )
    assert summary["current_commit"] == "aaaa1111aaaa"
    assert summary["remote_commit"] == "bbbb2222bbbb"
    assert "changed_files" not in summary


@pytest.mark.parametrize(
    ("last", "running", "expected"),
    (
        # 已更新到当时的目标提交（长 commit 与短 commit 前缀比较）
        (
            {"has_update": True, "current_commit": "aaaa1111aaaa", "remote_commit": "034100485772"},
            "034100485772" + "f" * 28,
            True,
        ),
        # 检查后服务器换过版本（目标又前进，但运行提交已不是检查时的提交）
        (
            {"has_update": True, "current_commit": "aaaa1111aaaa", "remote_commit": "bbbb2222bbbb"},
            "cccc3333cccc",
            True,
        ),
        # 运行提交仍是检查时的提交：结论有效
        (
            {"has_update": True, "current_commit": "aaaa1111aaaa", "remote_commit": "bbbb2222bbbb"},
            "aaaa1111aaaa" + "0" * 28,
            False,
        ),
        # 取不到运行提交：不做判断
        (
            {"has_update": True, "current_commit": "aaaa1111aaaa", "remote_commit": "bbbb2222bbbb"},
            None,
            False,
        ),
        # 老摘要没有 commit 字段：不做判断
        ({"has_update": True, "current_version": "1.0.0"}, "cccc3333cccc", False),
        # 本来就没有更新：无需判断
        (
            {"has_update": False, "current_commit": "aaaa1111aaaa", "remote_commit": "aaaa1111aaaa"},
            "cccc3333cccc",
            False,
        ),
        # updater 待部署状态（Git 已前进、容器未切换）：运行提交落后不算陈旧
        (
            {"has_update": True, "current_commit": "bbbb2222bbbb", "remote_commit": "bbbb2222bbbb"},
            "aaaa1111aaaa",
            False,
        ),
    ),
)
def test_is_last_check_stale(last, running, expected) -> None:
    assert app_update_service.is_last_check_stale(last, running) is expected


@pytest.mark.asyncio
async def test_update_status_marks_old_has_update_stale_after_deploy(
    db_factory, monkeypatch
) -> None:
    """服务器更新并重启后，旧的 has_update=true 不能继续点亮角标。"""

    monkeypatch.setattr(app_update_service, "AsyncSessionLocal", db_factory)
    async with db_factory() as db:
        await app_update_service.save_last_check(
            db,
            app_update_service.summarize_result(
                {
                    "has_update": True,
                    "current_version": "0.100.0-beta.6",
                    "target_version": "0.100.0-beta.7",
                    "current_commit": "aaaa1111aaaa",
                    "remote_commit": "034100485772",
                    "remote": "origin",
                    "branch": "Beta",
                }
            ),
        )
        await db.commit()

    async def deployed() -> str:
        return "034100485772" + "e" * 28

    monkeypatch.setattr(app_update_service, "current_runtime_commit", deployed)
    status = await app_update_service.load_update_status()
    assert status["last_check"]["has_update"] is False
    assert status["last_check"]["stale"] is True

    # 落库内容本身不被改写，下一次检查自然覆盖
    async with db_factory() as db:
        stored = await app_update_service.load_last_check(db)
        assert stored is not None and stored["has_update"] is True

    async def not_deployed() -> str:
        return "aaaa1111aaaa" + "0" * 28

    monkeypatch.setattr(app_update_service, "current_runtime_commit", not_deployed)
    status = await app_update_service.load_update_status()
    assert status["last_check"]["has_update"] is True
    assert "stale" not in status["last_check"]


@pytest.mark.asyncio
async def test_update_status_without_runtime_commit_keeps_result(db_factory, monkeypatch) -> None:
    monkeypatch.setattr(app_update_service, "AsyncSessionLocal", db_factory)
    async with db_factory() as db:
        await app_update_service.save_last_check(
            db,
            {"has_update": True, "current_commit": "aaaa1111aaaa", "remote_commit": "bbbb2222bbbb"},
        )
        await db.commit()

    async def unknown() -> None:
        return None

    monkeypatch.setattr(app_update_service, "current_runtime_commit", unknown)
    status = await app_update_service.load_update_status()
    assert status["last_check"]["has_update"] is True


@pytest.mark.asyncio
async def test_load_last_check_returns_none_when_missing(db_factory) -> None:
    async with db_factory() as db:
        assert await app_update_service.load_last_check(db) is None


# ── 手动检查写回 ─────────────────────────────────────────────────


def _patch_manual_check(monkeypatch, db_factory, plan: dict):
    from app.api import system_health as sh

    monkeypatch.setenv("TELEPILOT_UPDATE_BRANCH", "Beta")
    monkeypatch.setattr(sh, "AsyncSessionLocal", db_factory)
    monkeypatch.setattr(app_update_service, "AsyncSessionLocal", db_factory)
    monkeypatch.setattr(
        sh,
        "_detect_runtime_mode",
        lambda: (sh.RUNTIME_PROD_CONTAINER_WITH_UPDATER, "http://updater:8765", None),
    )
    monkeypatch.setattr(sh, "_updater_request", lambda path, payload=None, timeout=30: plan)

    async def default_branch(_payload):
        remote = getattr(_payload, "remote", None) or "origin"
        branch = getattr(_payload, "branch", None) or "Beta"
        return remote, branch, False

    monkeypatch.setattr(sh, "_resolve_update_request", default_branch)
    return sh


_UP_TO_DATE_PLAN = {
    "ok": True,
    "has_update": False,
    "current_version": "0.100.0-beta.7",
    "target_version": "0.100.0-beta.7",
    "current_commit": "034100485772",
    "remote_commit": "034100485772",
    "ahead": 0,
    "components": ["none"],
}


@pytest.mark.asyncio
async def test_manual_check_writes_back_last_check(db_factory, monkeypatch) -> None:
    """手动检查当前通道后，角标读到的必须是这次的结论，而不是旧的"有新版本"。"""

    sh = _patch_manual_check(monkeypatch, db_factory, _UP_TO_DATE_PLAN)
    async with db_factory() as db:
        await app_update_service.save_last_check(
            db,
            {"has_update": True, "current_version": "0.100.0-beta.6", "target_version": "0.100.0-beta.7"},
        )
        await db.commit()

    out = await sh.check_update(_user=None)  # type: ignore[arg-type]
    assert out.has_update is False

    async with db_factory() as db:
        stored = await app_update_service.load_last_check(db)
    assert stored is not None
    assert stored["has_update"] is False
    assert stored["current_commit"] == "034100485772"
    assert stored["branch"] == "Beta"
    assert stored["checked_at"]

    # 显式只传 full 也算检查当前通道
    out = await sh.check_update(_user=None, payload=sh.UpdateRequest(full=True))  # type: ignore[arg-type]
    assert out.has_update is False


@pytest.mark.asyncio
async def test_manual_check_with_override_does_not_write_back(db_factory, monkeypatch) -> None:
    """指定其他 remote / branch 的试探性检查不应覆盖当前通道的角标结论。"""

    sh = _patch_manual_check(monkeypatch, db_factory, _UP_TO_DATE_PLAN)
    original = {"has_update": True, "current_version": "0.100.0-beta.6"}
    async with db_factory() as db:
        await app_update_service.save_last_check(db, original)
        await db.commit()

    await sh.check_update(_user=None, payload=sh.UpdateRequest(branch="main"))  # type: ignore[arg-type]

    async with db_factory() as db:
        stored = await app_update_service.load_last_check(db)
    assert stored is not None and stored["has_update"] is True
    assert stored["current_version"] == "0.100.0-beta.6"


@pytest.mark.asyncio
async def test_manual_check_write_back_failure_keeps_response(db_factory, monkeypatch) -> None:
    sh = _patch_manual_check(monkeypatch, db_factory, _UP_TO_DATE_PLAN)

    class _Broken:
        def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(sh, "AsyncSessionLocal", lambda: _Broken())
    out = await sh.check_update(_user=None)  # type: ignore[arg-type]
    assert out.has_update is False
    assert out.current_commit == "034100485772"
