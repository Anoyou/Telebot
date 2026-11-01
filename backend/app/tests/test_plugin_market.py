"""插件市场（0.113.0）：来源、索引、并发更新、健康窗口回滚、自动更新候选。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models.feature import FEATURE_STATE_FAILED, AccountFeature, Feature
from app.db.models.plugin import PLUGIN_SOURCE_REPO, InstalledPlugin, PluginInstallHistory
from app.db.models.plugin_repo import PluginRepo
from app.db.models.system import SystemSetting
from app.services import plugin_market_service as svc
from app.services import plugin_repo_service as repo_svc
from app.services import remote_plugin_service as remote_svc
from app.tests._sqlite_engine import create_file_sqlite_engine

REPO_URL = "https://github.com/Anoyou/telebot-plugins.git"


def _write_plugin(root: Path, name: str, version: str, *, marker: str = "") -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "display_name": name,
                "description": f"{name} plugin",
                "author": "TelePilot",
                "version": version,
                "entry": "plugin.py",
                "requires_platform_capabilities": [],
            }
        ),
        encoding="utf-8",
    )
    for filename in ("manifest.py", "__init__.py"):
        (plugin_dir / filename).write_text("", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(f"# {version} {marker}\n", encoding="utf-8")
    return plugin_dir


def _registry(*entries: dict) -> dict:
    return {"schema_version": 1, "repository": REPO_URL, "trust": "official", "plugins": list(entries)}


def _entry(key: str, version: str, **extra) -> dict:
    return {"key": key, "name": {"zh": key, "en": key}, "kind": "utility", "version": version, **extra}


@pytest.fixture(autouse=True)
def _reset_market_cache(monkeypatch):
    svc.reset_cache()
    monkeypatch.delenv(svc.MARKET_ENV, raising=False)
    monkeypatch.delenv(svc.CONCURRENCY_ENV, raising=False)
    yield
    svc.reset_cache()


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_file_sqlite_engine(tmp_path / "market.db", busy_timeout_seconds=30)
    async with engine.begin() as connection:
        for model in (SystemSetting, InstalledPlugin, PluginInstallHistory, Feature, AccountFeature, PluginRepo):
            await connection.run_sync(model.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(svc, "AsyncSessionLocal", session_factory)

    async def _no_stats(_key):
        return None

    monkeypatch.setattr(svc, "_stats_totals", _no_stats)
    # plugin_install_history 主键是 BigInteger，SQLite 建表不会自增；这里只记录事件类型
    history: list[str] = []
    monkeypatch.setattr(svc, "record_plugin_install_history", lambda _db, *, row, event_type, **_kw: history.append(event_type))
    session_factory.history = history  # type: ignore[attr-defined]
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def _install_row(factory, installed_root: Path, name: str, version: str, *, source_url: str = REPO_URL) -> None:
    _write_plugin(installed_root, name, version, marker="installed")
    async with factory() as db:
        db.add(
            InstalledPlugin(
                key=name,
                source=PLUGIN_SOURCE_REPO,
                source_url=source_url,
                installed_path=str(installed_root / name),
                version=version,
                manifest_json={"name": name, "display_name": name, "version": version},
                enabled=False,
                trust_tier="community",
                source_label="Plugin Repo",
                lint_warnings=[],
            )
        )
        db.add(Feature(key=name, display_name=name, is_builtin=False, version=version, manifest=None))
        await db.commit()


def _fake_repo(monkeypatch, repo_dir: Path, calls: list[str] | None = None) -> None:
    async def _ensure(url, *, force_refresh=False, token=None):  # noqa: ARG001
        if calls is not None:
            calls.append(url)
        return repo_dir

    monkeypatch.setattr(repo_svc, "_ensure_repo_cached_unlocked", _ensure)


# ── 索引解析与来源 ──────────────────────────────────────────


def test_parse_registry_downgrades_trust_and_skips_bad_entries() -> None:
    source = {"id": "src-x", "name": "x", "trust": "community"}
    entries, repository = svc.parse_registry(
        _registry(
            _entry("good", "1.0.0", trust="official"),
            _entry("../bad", "1.0.0"),
            _entry("noversion", ""),
            _entry("traverse", "1.0.0", path="../etc"),
            _entry("other_repo", "1.0.0", repository="file:///etc/passwd"),
            _entry("good", "2.0.0"),
        ),
        source=source,
    )
    assert repository == REPO_URL
    assert [item["key"] for item in entries] == ["good"]
    assert entries[0]["trust"] == "community"  # 自定义来源不能把自己抬成 official
    with pytest.raises(ValueError):
        svc.parse_registry({"plugins": "nope"}, source=source)


async def test_sources_official_builtin_and_custom_rules(factory, monkeypatch) -> None:
    async with factory() as db:
        sources = await svc.list_sources(db)
        assert [item["id"] for item in sources] == ["official"]
        assert sources[0]["enabled"] is True and sources[0]["auto_update"] is False
        added = await svc.add_source(db, url="https://example.com/registry.json", name="社区", trust="community")
        with pytest.raises(svc.MarketError) as dup:
            await svc.add_source(db, url="https://example.com/registry.json")
        assert dup.value.code == "SOURCE_EXISTS"
        with pytest.raises(svc.MarketError):
            await svc.add_source(db, url="http://example.com/registry.json")
        with pytest.raises(svc.MarketError) as auto:
            await svc.update_source(db, added["id"], {"auto_update": True})
        assert auto.value.code == "AUTO_UPDATE_NOT_ALLOWED"
        await svc.update_source(db, "official", {"auto_update": True})
        await svc.update_source(db, added["id"], {"enabled": False})
        with pytest.raises(svc.MarketError) as builtin:
            await svc.delete_source(db, "official")
        assert builtin.value.code == "SOURCE_BUILTIN"
        await db.commit()
    async with factory() as db:
        sources = {item["id"]: item for item in await svc.list_sources(db)}
        assert sources["official"]["auto_update"] is True
        assert sources[added["id"]]["enabled"] is False
        assert sources[added["id"]]["trust"] == "community"
        await svc.delete_source(db, added["id"])
        assert [item["id"] for item in await svc.list_sources(db)] == ["official"]
    monkeypatch.setenv(svc.MARKET_ENV, "off")
    async with factory() as db:
        official = (await svc.list_sources(db))[0]
        assert official["enabled"] is False and official["available"] is False


async def test_catalog_uses_builtin_fallback_and_marks_updates(factory, monkeypatch, tmp_path) -> None:
    async def _fail(_url):
        raise RuntimeError("network down")

    monkeypatch.setattr(svc, "fetch_registry_json", _fail)
    async with factory() as db:
        catalog = await svc.build_catalog(db)
    official = catalog["sources"][0]
    assert official["status"] == "fallback" and official["fallback"] is True
    assert official["plugin_count"] > 30  # 随服务器打包的官方索引副本
    assert "network down" in (official["error"] or "")

    installed_root = tmp_path / "installed"
    await _install_row(factory, installed_root, "alpha", "1.0.0")
    await _install_row(factory, installed_root, "beta", "1.0.0")
    await _install_row(factory, installed_root, "gamma", "1.0.0", source_url="https://github.com/other/repo.git")

    async def _ok(_url):
        return _registry(
            _entry("alpha", "1.1.0"),
            _entry("beta", "1.2.0"),
            _entry("gamma", "9.0.0"),
            _entry("future", "1.0.0", min_telepilot_version="99.0.0"),
        )

    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    async with factory() as db:
        await svc.set_pinned(db, "beta", pinned=True)
        await db.commit()
        catalog = await svc.build_catalog(db, refresh=True)
    items = {item["key"]: item for item in catalog["items"]}
    assert items["alpha"]["update_available"] is True
    assert items["beta"]["update_available"] is True and items["beta"]["pinned_version"] == "1.0.0"
    assert items["gamma"]["update_available"] is False  # 来自别的仓库，不提示更新
    assert items["gamma"]["installed"]["same_source"] is False
    assert items["future"]["compatible"] is False and "99.0.0" in items["future"]["incompatible_reason"]
    assert catalog["updates_available"] == 1  # 固定版本的不计入角标
    assert catalog["sources"][0]["status"] == "ok"


# ── 并发更新 ────────────────────────────────────────────────


async def test_bulk_update_runs_in_parallel_with_limit_and_isolates_failures(factory, monkeypatch, tmp_path) -> None:
    installed_root = tmp_path / "installed"
    repo_dir = tmp_path / "repo"
    names = [f"p{index}" for index in range(5)]
    for name in names:
        await _install_row(factory, installed_root, name, "1.0.0")
        _write_plugin(repo_dir, name, "1.1.0", marker="new")
    # p3 的新版缺少运行期文件 → 这一个失败，其它照常更新
    (repo_dir / "p3" / "manifest.py").unlink()
    fetches: list[str] = []
    _fake_repo(monkeypatch, repo_dir, fetches)

    active = 0
    peak = 0
    reloaded: list[str] = []

    async def _reload(_db, name):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        reloaded.append(name)

    monkeypatch.setattr(remote_svc, "trigger_reload", _reload)
    targets = [svc.UpdateTarget(key=name, repo_url=REPO_URL, to_version="1.1.0") for name in names]
    run = await svc.run_updates(targets, concurrency=3, trigger="manual")
    by_key = {item.key: item for item in run.items}
    assert [item.key for item in run.items] == names
    assert {key for key, item in by_key.items() if item.status == "updated"} == {"p0", "p1", "p2", "p4"}
    assert by_key["p3"].status == "failed" and "manifest.py" in by_key["p3"].message
    assert 1 < peak <= 3
    assert len(fetches) == 1  # 同一仓库只拉取一次
    assert sorted(reloaded) == ["p0", "p1", "p2", "p4"]
    async with factory() as db:
        rows = {row.key: row.version for row in (await db.execute(select(InstalledPlugin))).scalars()}
        rollouts = await svc.load_rollouts(db)
    assert rows == {"p0": "1.1.0", "p1": "1.1.0", "p2": "1.1.0", "p3": "1.0.0", "p4": "1.1.0"}
    assert set(rollouts) == {"p0", "p1", "p2", "p4"}
    assert all(item["status"] == "watching" for item in rollouts.values())
    assert (installed_root / ".previous" / "p0" / "plugin.py").read_text().startswith("# 1.0.0")
    assert (installed_root / "p0" / "plugin.py").read_text().startswith("# 1.1.0")


async def test_update_from_market_reports_pinned_and_skips_it_in_update_all(factory, monkeypatch, tmp_path) -> None:
    installed_root = tmp_path / "installed"
    repo_dir = tmp_path / "repo"
    for name in ("alpha", "beta"):
        await _install_row(factory, installed_root, name, "1.0.0")
        _write_plugin(repo_dir, name, "1.1.0")
    _fake_repo(monkeypatch, repo_dir)

    async def _ok(_url):
        return _registry(_entry("alpha", "1.1.0"), _entry("beta", "1.1.0"))

    async def _reload(_db, _name):
        return None

    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    monkeypatch.setattr(remote_svc, "trigger_reload", _reload)
    async with factory() as db:
        await svc.set_pinned(db, "beta", pinned=True)
        await db.commit()
    async with factory() as db:
        explicit = await svc.update_from_market(db, keys=["beta"])
    assert [(item.key, item.status) for item in explicit.items] == [("beta", "pinned")]
    async with factory() as db:
        everything = await svc.update_from_market(db, keys=None)
    assert [(item.key, item.status) for item in everything.items] == [("alpha", "updated")]
    assert everything.summary()["updated"] == 1


async def test_repo_bulk_update_uses_parallel_engine(factory, monkeypatch, tmp_path) -> None:
    installed_root = tmp_path / "installed"
    repo_dir = tmp_path / "repo"
    await _install_row(factory, installed_root, "alpha", "1.0.0")
    await _install_row(factory, installed_root, "beta", "1.1.0")
    _write_plugin(repo_dir, "alpha", "1.1.0")
    _write_plugin(repo_dir, "beta", "1.1.0")
    _write_plugin(repo_dir, "not_installed", "1.0.0")
    _fake_repo(monkeypatch, repo_dir)

    async def _reload(_db, _name):
        raise remote_svc.RemotePluginError("PLUGIN_RELOAD_UNCONFIRMED", "账号 1 的 worker 未确认重载")

    monkeypatch.setattr(remote_svc, "trigger_reload", _reload)
    async with factory() as db:
        db.add(PluginRepo(id=7, name="官方", url=REPO_URL, description=""))
        await db.commit()
    async with factory() as db:
        result = await svc.update_repo_installed(db, 7)
    assert result["checked"] == 2 and result["update_available"] == 1
    assert result["updated"] == 1 and result["skipped"] == 1 and result["failed"] == 0
    updated = next(item for item in result["items"] if item["status"] == "updated")
    assert updated["name"] == "alpha" and "未确认重载" in updated["message"]
    assert result["concurrency"] == 3


# ── 健康窗口与回滚 ──────────────────────────────────────────


def test_evaluate_health_rules() -> None:
    now = datetime.now(UTC)
    later = now + timedelta(minutes=5)
    base = {"calls": 100, "fail": 2, "timeout": 0, "circuit_open": 0}
    assert svc.evaluate_health(baseline=base, current=base, new_failures={}, now=now, deadline=later) == ("watching", None)
    status, reason = svc.evaluate_health(baseline=base, current=base, new_failures={"1": "ImportError"}, now=now, deadline=later)
    assert status == "unhealthy" and "加载失败" in reason
    status, reason = svc.evaluate_health(
        baseline=base, current={**base, "calls": 105, "circuit_open": 3}, new_failures={}, now=now, deadline=later
    )
    assert status == "unhealthy" and "熔断" in reason
    status, reason = svc.evaluate_health(
        baseline=base, current={"calls": 120, "fail": 12, "timeout": 2, "circuit_open": 0}, new_failures={}, now=now, deadline=later
    )
    assert status == "unhealthy" and "失败率" in reason
    # 少量失败、调用太少：不判不健康
    assert svc.evaluate_health(
        baseline=base, current={"calls": 105, "fail": 5, "timeout": 0, "circuit_open": 0}, new_failures={}, now=now, deadline=later
    )[0] == "watching"
    # 更新前本来就经常失败：失败率没有显著升高
    noisy = {"calls": 100, "fail": 40, "timeout": 0, "circuit_open": 0}
    assert svc.evaluate_health(
        baseline=noisy, current={"calls": 120, "fail": 50, "timeout": 0, "circuit_open": 0}, new_failures={}, now=now, deadline=later
    )[0] == "watching"
    assert svc.evaluate_health(baseline=None, current=None, new_failures={}, now=later, deadline=now) == ("healthy", None)


async def _updated_alpha(factory, monkeypatch, tmp_path) -> Path:
    installed_root = tmp_path / "installed"
    repo_dir = tmp_path / "repo"
    await _install_row(factory, installed_root, "alpha", "1.0.0")
    _write_plugin(repo_dir, "alpha", "2.0.0", marker="new")
    _fake_repo(monkeypatch, repo_dir)

    async def _reload(_db, _name):
        return None

    monkeypatch.setattr(remote_svc, "trigger_reload", _reload)
    run = await svc.run_updates([svc.UpdateTarget(key="alpha", repo_url=REPO_URL)], concurrency=3, trigger="auto")
    assert run.items[0].status == "updated" and run.items[0].health_check is True
    return installed_root


async def test_unhealthy_update_rolls_back_and_alerts(factory, monkeypatch, tmp_path) -> None:
    installed_root = await _updated_alpha(factory, monkeypatch, tmp_path)
    alerts: list[str] = []

    async def _alert(key, text, reason):  # noqa: ARG001
        alerts.append(text)

    monkeypatch.setattr(svc, "_alert", _alert)
    async with factory() as db:
        db.add(AccountFeature(account_id=1, feature_key="alpha", enabled=True, state=FEATURE_STATE_FAILED, last_error="ImportError: boom"))
        await db.commit()
    changed = await svc.check_rollouts()
    assert changed and changed[0]["status"] == "rolled_back"
    assert "加载失败" in changed[0]["reason"]
    assert (installed_root / "alpha" / "plugin.py").read_text().startswith("# 1.0.0")
    assert not (installed_root / ".previous" / "alpha").exists()
    async with factory() as db:
        row = await db.get(InstalledPlugin, "alpha")
        feature = (await db.execute(select(Feature).where(Feature.key == "alpha"))).scalar_one()
        rollouts = await svc.load_rollouts(db)
    assert row.version == "1.0.0" and feature.version == "1.0.0"
    assert factory.history == ["updated", "rolled_back"]
    assert rollouts["alpha"]["status"] == "rolled_back" and "previous" not in rollouts["alpha"]
    assert alerts and "自动回滚" in alerts[0] and "2.0.0 → 1.0.0" in alerts[0]
    # 回滚过的版本不会被自动更新再次装上
    catalog = {
        "sources": [{"id": "official", "auto_update": True}],
        "items": [
            {"key": "alpha", "source_id": "official", "update_available": True, "auto_update": True,
             "trust": "official", "pinned_version": None, "version": "2.0.0"},
        ],
    }
    assert svc.auto_update_candidates(catalog, rollouts) == []
    catalog["items"][0]["version"] = "2.0.1"
    assert [item["key"] for item in svc.auto_update_candidates(catalog, rollouts)] == ["alpha"]


async def test_healthy_window_end_removes_previous_dir(factory, monkeypatch, tmp_path) -> None:
    installed_root = await _updated_alpha(factory, monkeypatch, tmp_path)
    assert (installed_root / ".previous" / "alpha").is_dir()
    assert await svc.check_rollouts() == []  # 窗口未结束
    changed = await svc.check_rollouts(now=datetime.now(UTC) + timedelta(minutes=11))
    assert changed[0]["status"] == "healthy"
    assert not (installed_root / ".previous" / "alpha").exists()
    assert (installed_root / "alpha" / "plugin.py").read_text().startswith("# 2.0.0")


async def test_manual_rollback_and_accept_require_active_window(factory, monkeypatch, tmp_path) -> None:
    installed_root = await _updated_alpha(factory, monkeypatch, tmp_path)

    async def _alert(*_args):
        return None

    monkeypatch.setattr(svc, "_alert", _alert)
    result = await svc.rollback("alpha", reason="管理员手动回滚", automatic=False)
    assert result["status"] == "rolled_back"
    assert (installed_root / "alpha" / "plugin.py").read_text().startswith("# 1.0.0")
    with pytest.raises(svc.MarketError):
        await svc.rollback("alpha", reason="again", automatic=False)
    with pytest.raises(svc.MarketError):
        await svc.accept_rollout("alpha")


def test_auto_update_candidates_require_official_auto_source_and_unpinned() -> None:
    base = {"update_available": True, "auto_update": True, "trust": "official", "pinned_version": None, "version": "1.1.0"}
    catalog = {
        "sources": [{"id": "official", "auto_update": True}, {"id": "src-c", "auto_update": False}],
        "items": [
            {**base, "key": "a", "source_id": "official"},
            {**base, "key": "b", "source_id": "official", "pinned_version": "1.0.0"},
            {**base, "key": "c", "source_id": "src-c", "trust": "community"},
            {**base, "key": "d", "source_id": "official", "update_available": False},
        ],
    }
    rollouts = {"e": {"status": "watching"}}
    catalog["items"].append({**base, "key": "e", "source_id": "official"})
    assert [item["key"] for item in svc.auto_update_candidates(catalog, rollouts)] == ["a"]


async def test_market_routes_require_login() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/plugin-market/catalog")
        assert response.status_code == 401


# ── 0.117.0：详情字段、稳定 / 测试通道、后台定时拉取 ─────────────────

BETA_URL = "https://github.com/Anoyou/telebot-plugins/tree/beta"


def test_parse_registry_reads_detail_fields_and_drops_unsafe_urls() -> None:
    source = {"id": "official", "name": "官方", "trust": "official"}
    entries, _ = svc.parse_registry(
        _registry(
            _entry(
                "alpha",
                "1.0.0",
                summary="简介",
                summary_i18n={"en": "Intro"},
                description="完整简介",
                usage="发送 {prefix}alpha",
                commands=["alpha", 3],
                release_notes="- 新功能",
                released_at="2026-11-01",
                homepage="https://github.com/Anoyou/telebot-plugins/tree/main/alpha",
                license="MIT",
                icon_url="http://insecure.example/icon.png",
                screenshots=["https://example.com/1.png", "javascript:alert(1)", "https://u:p@example.com/2.png"],
                channel_versions={"stable": "1.0.0", "beta": "1.1.0-beta.1"},
            ),
            _entry("legacy", "1.0.0", summary="旧索引"),
        ),
        source=source,
    )
    alpha, legacy = entries
    assert alpha["summary_i18n"] == {"en": "Intro", "zh": "简介"}
    assert alpha["commands"] == ["alpha"]
    assert alpha["icon_url"] is None  # 只收 https
    assert alpha["screenshots"] == ["https://example.com/1.png"]
    assert alpha["channel_versions"]["beta"] == "1.1.0-beta.1"
    # 旧索引没有新字段：给空值，照常显示
    assert legacy["description"] is None and legacy["screenshots"] == [] and legacy["summary_i18n"] == {"zh": "旧索引"}


def test_channel_urls_and_install_spec() -> None:
    entry = {
        "key": "alpha",
        "version": "1.0.0",
        "repository": REPO_URL,
        "channels": {"stable": "main", "beta": "beta", "same": "main", "bad": "../x", "nightly": "dev/next"},
        "channel_versions": {"beta": "1.1.0-beta.1"},
    }
    assert svc.channel_url(entry, "stable") == REPO_URL
    assert svc.channel_url(entry, "beta") == BETA_URL
    assert svc.channel_url(entry, "same") is None  # 与稳定通道同一分支
    assert svc.channel_url(entry, "bad") is None
    assert svc.channel_url(entry, "missing") is None
    names = [(ch["name"], ch["version"]) for ch in svc.available_channels(entry)]
    assert names == [("stable", "1.0.0"), ("beta", "1.1.0-beta.1"), ("nightly", None)]
    gitlab = {**entry, "repository": "https://gitlab.com/me/plugins.git"}
    assert [ch["name"] for ch in svc.available_channels(gitlab)] == ["stable"]  # 非 GitHub 只有稳定通道
    assert svc.parse_install_spec("registry:alpha#beta") == ("alpha", None, "beta")
    assert svc.parse_install_spec("alpha@1.0.0") == ("alpha", "1.0.0", None)
    assert svc.parse_install_spec(" alpha ") == ("alpha", None, None)


async def test_catalog_tracks_installed_channel_and_beta_updates(factory, monkeypatch, tmp_path) -> None:
    installed_root = tmp_path / "installed"
    await _install_row(factory, installed_root, "alpha", "1.0.0")
    await _install_row(factory, installed_root, "beta_user", "1.1.0-beta.1", source_url=BETA_URL)

    async def _ok(_url):
        channels = {"stable": "main", "beta": "beta"}
        return _registry(
            _entry("alpha", "1.0.0", channels=channels, channel_versions={"stable": "1.0.0", "beta": "1.1.0-beta.1"}),
            _entry("beta_user", "1.0.0", channels=channels, channel_versions={"stable": "1.0.0", "beta": "1.1.0-beta.2"}),
        )

    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    async with factory() as db:
        catalog = await svc.build_catalog(db, refresh=True)
    items = {item["key"]: item for item in catalog["items"]}
    assert items["alpha"]["channel"] == "stable" and items["alpha"]["update_available"] is False
    assert [ch["name"] for ch in items["alpha"]["available_channels"]] == ["stable", "beta"]
    beta = items["beta_user"]
    assert beta["channel"] == "beta" and beta["installed"]["same_source"] is True
    assert beta["update_available"] is True and beta["update_version"] == "1.1.0-beta.2"
    assert beta["auto_update"] is False  # 测试通道不自动更新
    assert catalog["registry_refresh_hours"] == 6


async def test_install_with_channel_and_version_check(factory, monkeypatch, tmp_path) -> None:
    async def _ok(_url):
        return _registry(
            _entry("alpha", "1.0.0", channels={"stable": "main", "beta": "beta"}, channel_versions={"beta": "1.1.0"})
        )

    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    calls: list[tuple[str, str]] = []

    async def _ensure(url, *, force_refresh=False, token=None):  # noqa: ARG001
        return tmp_path

    async def _install(db, row, repo_dir, key, **_kw):  # noqa: ARG001
        calls.append((row.url, key))
        return "view"

    monkeypatch.setattr(repo_svc, "_ensure_repo_cached_unlocked", _ensure)
    monkeypatch.setattr(repo_svc, "_install_plugin_from_cached_repo", _install)
    async with factory() as db:
        assert await svc.install(db, source_id="official", key="registry:alpha#beta") == "view"
        assert await svc.install(db, source_id="official", key="alpha", version="1.0.0") == "view"
        with pytest.raises(svc.MarketError) as missing:
            await svc.install(db, source_id="official", key="alpha", channel="nightly")
        with pytest.raises(svc.MarketError) as old:
            await svc.install(db, source_id="official", key="alpha@0.9.0")
    assert calls == [(BETA_URL, "alpha"), (REPO_URL, "alpha")]
    assert missing.value.code == "CHANNEL_NOT_FOUND"
    assert old.value.code == "VERSION_NOT_AVAILABLE"


async def test_switch_channel_replaces_with_health_window_and_back(factory, monkeypatch, tmp_path) -> None:
    installed_root = tmp_path / "installed"
    await _install_row(factory, installed_root, "alpha", "1.0.0")
    stable_dir = tmp_path / "stable"
    beta_dir = tmp_path / "beta"
    _write_plugin(stable_dir, "alpha", "1.0.0", marker="stable")
    _write_plugin(beta_dir, "alpha", "1.1.0-beta.1", marker="beta")
    fetched: list[str] = []

    async def _ensure(url, *, force_refresh=False, token=None):  # noqa: ARG001
        fetched.append(url)
        return beta_dir if url == BETA_URL else stable_dir

    async def _ok(_url):
        return _registry(
            _entry("alpha", "1.0.0", channels={"stable": "main", "beta": "beta"},
                   channel_versions={"stable": "1.0.0", "beta": "1.1.0-beta.1"})
        )

    async def _reload(_db, _name):
        return None

    monkeypatch.setattr(repo_svc, "_ensure_repo_cached_unlocked", _ensure)
    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    monkeypatch.setattr(remote_svc, "trigger_reload", _reload)

    async with factory() as db:
        run = await svc.switch_channel(db, key="alpha", channel="beta")
    assert [(item.status, item.to_version, item.health_check) for item in run.items] == [("updated", "1.1.0-beta.1", True)]
    assert run.items[0].message.startswith("已切换通道")
    assert (installed_root / "alpha" / "plugin.py").read_text().startswith("# 1.1.0-beta.1")
    async with factory() as db:
        row = await db.get(InstalledPlugin, "alpha")
        assert row.source_url == BETA_URL and row.version == "1.1.0-beta.1"
        rollouts = await svc.load_rollouts(db)
        assert rollouts["alpha"]["trigger"] == "channel" and rollouts["alpha"]["status"] == "watching"
        catalog = await svc.build_catalog(db)
        assert catalog["items"][0]["channel"] == "beta"
        with pytest.raises(svc.MarketError) as same:
            await svc.switch_channel(db, key="alpha", channel="beta")
        assert same.value.code == "ALREADY_ON_CHANNEL"
    await svc.accept_rollout("alpha")
    # 切回稳定通道：版本更低也照样替换（降级），仍然进入健康观察
    async with factory() as db:
        back = await svc.switch_channel(db, key="alpha", channel="stable")
    assert [(item.status, item.to_version) for item in back.items] == [("updated", "1.0.0")]
    async with factory() as db:
        row = await db.get(InstalledPlugin, "alpha")
        assert row.source_url == REPO_URL and row.version == "1.0.0"
    assert fetched == [BETA_URL, REPO_URL]


async def test_switch_channel_refuses_pinned_and_foreign(factory, monkeypatch, tmp_path) -> None:
    installed_root = tmp_path / "installed"
    await _install_row(factory, installed_root, "alpha", "1.0.0")
    await _install_row(factory, installed_root, "other", "1.0.0", source_url="https://github.com/someone/else.git")

    async def _ok(_url):
        channels = {"stable": "main", "beta": "beta"}
        return _registry(_entry("alpha", "1.0.0", channels=channels), _entry("other", "1.0.0", channels=channels))

    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    async with factory() as db:
        await svc.set_pinned(db, "alpha", pinned=True)
        await db.commit()
        with pytest.raises(svc.MarketError) as pinned:
            await svc.switch_channel(db, key="alpha", channel="beta")
        with pytest.raises(svc.MarketError) as foreign:
            await svc.switch_channel(db, key="other", channel="beta")
        with pytest.raises(svc.MarketError) as missing:
            await svc.switch_channel(db, key="ghost", channel="beta")
    assert pinned.value.code == "PLUGIN_PINNED"
    assert foreign.value.code == "NOT_SAME_SOURCE"
    assert missing.value.code == "PLUGIN_NOT_IN_MARKET"


async def test_registry_refresh_tick_fetches_enabled_sources_once_per_ttl(factory, monkeypatch) -> None:
    fetched: list[str] = []

    async def _ok(url):
        fetched.append(url)
        return _registry(_entry("alpha", "1.0.0"))

    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    async with factory() as db:
        await svc.add_source(db, url="https://example.com/registry.json", trust="community")
        disabled = await svc.add_source(db, url="https://example.org/registry.json")
        await svc.update_source(db, disabled["id"], {"enabled": False})
        await db.commit()
    assert await svc.registry_refresh_tick() == 2
    assert sorted(fetched) == sorted([svc.OFFICIAL_REGISTRY_URL, "https://example.com/registry.json"])
    await svc.registry_refresh_tick()  # 缓存 6 小时内有效，不重复下载
    assert len(fetched) == 2


async def test_registry_refresh_tick_respects_market_off(factory, monkeypatch) -> None:
    monkeypatch.setenv(svc.MARKET_ENV, "off")
    fetched: list[str] = []

    async def _ok(url):
        fetched.append(url)
        return _registry()

    monkeypatch.setattr(svc, "fetch_registry_json", _ok)
    assert await svc.registry_refresh_tick() == 0
    assert fetched == []


def test_auto_update_skips_beta_channel() -> None:
    base = {"update_available": True, "auto_update": True, "trust": "official", "pinned_version": None,
            "version": "1.1.0", "source_id": "official"}
    catalog = {
        "sources": [{"id": "official", "auto_update": True}],
        "items": [{**base, "key": "a", "channel": "stable"}, {**base, "key": "b", "channel": "beta"}],
    }
    assert [item["key"] for item in svc.auto_update_candidates(catalog, {})] == ["a"]
