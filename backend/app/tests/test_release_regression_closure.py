from __future__ import annotations

import asyncio
import importlib.util
import itertools
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.feature import AccountFeature
from app.db.models.system import SystemSetting
from app.services import plugin_install_service, remote_plugin_service
from app.services.system_agent import model_capability
from app.tests._sqlite_engine import create_file_sqlite_engine
from app.worker.plugins import loader

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(params=["sqlite", "postgresql"])
async def cache_database(request, tmp_path):
    schema = None
    if request.param == "postgresql":
        url = os.getenv("TELEPILOT_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("设置 TELEPILOT_TEST_POSTGRES_URL 运行真实 PostgreSQL 并发回归")
        schema = f"closure_{uuid4().hex}"
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_file_sqlite_engine(tmp_path / "cache.sqlite")
    try:
        async with engine.begin() as connection:
            if schema:
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.run_sync(SystemSetting.__table__.create)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()


@pytest.mark.parametrize("service", [plugin_install_service, remote_plugin_service])
def test_staging_cleanup_preserves_registered_directory_shapes(tmp_path, service) -> None:
    names = ["demo.installing", "demo.installing.12345678", "demo.installinger.installing"]
    for name in names:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "plugin.json").write_text(json.dumps({"key": name}))
        os.utime(directory, (time.time() - 7200, time.time() - 7200))
    service._cleanup_staging_residue(tmp_path / "demo")
    assert all((tmp_path / name / "plugin.json").exists() for name in names)


@pytest.mark.asyncio
async def test_failed_plugin_rollback_does_not_poison_next_activation(tmp_path, monkeypatch) -> None:
    engine = create_file_sqlite_engine(tmp_path / "features.sqlite")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AccountFeature.__table__.create)
        async with factory() as db:
            db.add_all([
                AccountFeature(account_id=41, feature_key="broken", enabled=True),
                AccountFeature(account_id=41, feature_key="healthy", enabled=True),
            ])
            await db.commit()
        activated = []

        async def activate(db, state, feature, redis):
            if feature.feature_key == "broken":
                raise RuntimeError("constructor failed")
            activated.append(feature.feature_key)

        monkeypatch.setattr(loader, "_activate", activate)
        monkeypatch.setattr(loader, "_log", AsyncMock())
        async with factory() as db:
            features = (await db.execute(select(AccountFeature))).scalars().all()
            state = loader._AccountState(41)
            for feature_key in [feature.feature_key for feature in features]:
                feature = await db.get(AccountFeature, (41, feature_key), populate_existing=True)
                await loader._activate_isolated(db, state, feature, None)
        assert activated == ["healthy"]
        async with factory() as db:
            broken = await db.get(AccountFeature, (41, "broken"))
            assert broken.state == loader.FEATURE_STATE_FAILED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_capability_writes_commit_before_next_writer_reads(cache_database, monkeypatch) -> None:
    factory = cache_database
    first_flushed = asyncio.Event()
    release_first = asyncio.Event()
    original_flush = model_capability._persist_cache_entries
    try:
        async with factory() as db:
            db.add(SystemSetting(key=model_capability.CACHE_KEY, value={"version": 1, "entries": {}}))
            await db.commit()

        async def pause_after_flush(db, entries):
            await original_flush(db, entries)
            if "signature-a" in entries and "signature-b" not in entries:
                first_flushed.set()
                await release_first.wait()

        monkeypatch.setattr(model_capability, "_persist_cache_entries", pause_after_flush)
        async with factory() as first, factory() as second:
            async def write(db, signature):
                await model_capability._upsert_cache_entries(
                    db, {signature: {"status": "supported", "checked_at": "2026-10-24T00:00:00Z"}},
                )
                await asyncio.sleep(0.1)
                await db.commit()

            first_task = asyncio.create_task(write(first, "signature-a"))
            await asyncio.wait_for(first_flushed.wait(), 5)
            second_task = asyncio.create_task(write(second, "signature-b"))
            release_first.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), 10)
        async with factory() as db:
            row = await db.get(SystemSetting, model_capability.CACHE_KEY)
            assert set(row.value["entries"]) == {"signature-a", "signature-b"}
    finally:
        release_first.set()


@pytest.mark.parametrize("script,marker", [
    ("scripts/prod-up.sh", '  $PY - "$MK" "$JS" <<\'PY\'\n'),
    ("scripts/_lib.sh", '  "$py" - "$env_file" <<\'PY\'\n'),
    ("scripts/_lib.sh", '  "$py" - "$env_file" "$key" "$value" <<\'PY\'\n'),
])
def test_secret_temporary_file_is_private_before_first_write(tmp_path, monkeypatch, script, marker) -> None:
    source = (ROOT / script).read_text().split(marker, 1)[1].split("\nPY", 1)[0]
    env_path = tmp_path / ".env"
    env_path.write_text("MASTER_KEY=changeme\nJWT_SECRET=changeme\n")
    env_path.chmod(0o600)
    monkeypatch.chdir(tmp_path)
    arguments = ["script", "dummy-master", "dummy-jwt"] if script.endswith("prod-up.sh") else [
        "script", str(env_path), "TELEPILOT_WEB_IMAGE", "dummy-image",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    original_umask = os.umask(0o022)
    original_replace = os.replace

    def assert_no_readable_secret(source_path, destination):
        assert Path(source_path).stat().st_mode & 0o777 == 0o600
        return original_replace(source_path, destination)

    monkeypatch.setattr(os, "replace", assert_no_readable_secret)
    original_write = Path.write_text

    def reject_public_write(path, *args, **kwargs):
        result = original_write(path, *args, **kwargs)
        assert path.stat().st_mode & 0o777 == 0o600
        return result

    monkeypatch.setattr(Path, "write_text", reject_public_write)
    original_fdopen = os.fdopen

    def private_fdopen(descriptor, *args, **kwargs):
        assert os.fstat(descriptor).st_mode & 0o777 == 0o600
        return original_fdopen(descriptor, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", private_fdopen)
    try:
        exec(compile(source, script, "exec"), {})
        assert env_path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(original_umask)


def test_updater_watchdog_preserves_complete_handoff_success(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("updater_regression", ROOT / "deploy/updater/server.py")
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    monkeypatch.setattr(updater, "WORKSPACE", tmp_path)
    stream = SimpleNamespace(fileno=lambda: 177)
    process = SimpleNamespace(
        stdout=stream, terminate=Mock(), wait=Mock(return_value=-15), kill=Mock(), returncode=-15,
    )
    selections = itertools.count()

    def select_stream(*args):
        if next(selections) == 0:
            return [stream], [], []
        updater._persist_job("job", {
            "job_id": "job", "status": "succeeded", "progress": 100,
            "new_commit": "correct-commit", "returncode": 0, "logs": ["handoff completed"],
        })
        return [], [], []

    monkeypatch.setattr(updater, "_check_plan", lambda *args: {"ok": True, "has_update": True})
    monkeypatch.setattr(updater, "_apply_job_env", lambda *args: {})
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(updater.select, "select", select_stream)
    monkeypatch.setattr(updater.os, "read", lambda *args: "@@TELEPILOT_PROGRESS@@95|等待交接|handoff\n".encode())
    clock = itertools.count(1000)
    monkeypatch.setattr(updater.time, "time", lambda: next(clock))
    monkeypatch.setattr(updater, "_handoff_active", lambda: True)
    monkeypatch.setattr(updater, "_handoff_log_mtime", lambda: 0)
    monkeypatch.setattr(updater, "HANDOFF_STUCK_SECONDS", 0)
    updater._run_apply_job("job", "origin", "Beta", False)
    result = json.loads(updater._job_path("job").read_text())
    assert result["status"] == "succeeded"
    assert result["new_commit"] == "correct-commit"
    assert result["progress"] == 100
    assert result["returncode"] == 0
    updater._set_job("job", status="running", progress=95)
    assert updater._jobs["job"]["status"] == "succeeded"
    assert json.loads(updater._job_path("job").read_text())["new_commit"] == "correct-commit"


@pytest.mark.asyncio
async def test_capability_cache_does_not_commit_caller_transaction(cache_database):
    factory = cache_database
    async with factory() as caller:
        caller.add(SystemSetting(key="caller_uncommitted", value={}))
        await model_capability._upsert_cache_entries(caller, {
            "signature": {"status": "supported", "checked_at": "2026-10-24T00:00:00Z"},
        })
        async with factory() as observer:
            assert await observer.get(SystemSetting, "caller_uncommitted") is None
            assert await observer.get(SystemSetting, model_capability.CACHE_KEY) is not None
        await caller.rollback()


@pytest.mark.asyncio
async def test_capability_concurrent_first_writes_create_one_merged_row(cache_database):
    factory = cache_database

    async def write(signature):
        async with factory() as db:
            await model_capability._upsert_cache_entries(db, {
                signature: {"status": "supported", "checked_at": "2026-10-24T00:00:00Z"},
            })

    signatures = {f"signature-{number}" for number in range(8)}
    await asyncio.wait_for(asyncio.gather(*(write(signature) for signature in signatures)), 10)
    async with factory() as db:
        row = await db.get(SystemSetting, model_capability.CACHE_KEY)
        assert set(row.value["entries"]) == signatures
