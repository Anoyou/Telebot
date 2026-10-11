from __future__ import annotations

from pathlib import Path

from app import settings as settings_module
from app.settings import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(
        master_key="test-master-key",
        jwt_secret="test-jwt-secret",
        **overrides,
    )


def test_container_legacy_plugins_path_is_normalized(monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", Path("/app"))

    settings = _settings(plugins_installed_dir="/plugins/installed")

    assert settings.plugins_installed_path == Path("/app/plugins/installed")


def test_container_legacy_plugin_repo_cache_path_is_normalized(monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", Path("/app"))
    settings = _settings(plugin_repos_cache_dir="/data/plugin_repos")

    assert settings.resolve_project_path(settings.plugin_repos_cache_dir) == Path("/app/data/plugin_repos")


def test_non_legacy_absolute_path_is_kept(monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", Path("/app"))
    settings = _settings(plugins_installed_dir="/mnt/telepilot/plugins")

    assert settings.plugins_installed_path == Path("/mnt/telepilot/plugins")


def test_database_url_sync_pins_psycopg2_driver() -> None:
    """SQLAlchemy 2.1 起裸 ``postgresql://`` 会解析到 psycopg 3。

    镜像里只装了 ``psycopg2-binary``，若不同步 DSN 显式带 ``+psycopg2``，
    启动期 ``alembic upgrade head`` 会抛 ModuleNotFoundError，web 一直 unhealthy。
    """

    settings = _settings(
        database_url="postgresql+asyncpg://telebot:secret@postgres:5432/telebot"
    )

    assert (
        settings.database_url_sync
        == "postgresql+psycopg2://telebot:secret@postgres:5432/telebot"
    )


def test_database_url_sync_handles_bare_postgresql_and_sqlite() -> None:
    bare = _settings(database_url="postgresql://telebot:secret@db:5432/telebot")
    assert bare.database_url_sync.startswith("postgresql+psycopg2://")

    already_sync = _settings(database_url="postgresql+psycopg2://u:p@db:5432/telebot")
    assert already_sync.database_url_sync == "postgresql+psycopg2://u:p@db:5432/telebot"

    sqlite = _settings(database_url="sqlite+aiosqlite:///./test.db")
    assert sqlite.database_url_sync == "sqlite+aiosqlite:///./test.db"
