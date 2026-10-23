from __future__ import annotations

import asyncio
import json
from base64 import b64decode
from types import SimpleNamespace

import pytest

from app.services import plugin_repo_service as svc
from app.services.remote_plugin_service import GitOperationFailed


def _write_repo_plugin(repo, name: str, *, version: str = "1.0.0", tags: list[str] | None = None) -> None:
    plugin_dir = repo / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "display_name": name.replace("_", " ").title(),
                "description": f"{name} plugin",
                "author": "TelePilot Official",
                "version": version,
                "entry": "plugin.py",
                "tags": tags or [],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_install_plugin_from_repo_rejects_builtin_key(tmp_path) -> None:
    """仓库插件 key 与 builtin（如 forward）冲突时安装必须被拒，错误码与 ZIP 通道一致。"""
    repo = tmp_path / "repo"
    _write_repo_plugin(repo, "forward")
    # 补齐运行期结构（manifest.py / plugin.py / __init__.py），让冲突检查前的校验通过。
    (repo / "forward" / "manifest.py").write_text(
        "from app.worker.plugins.manifest import Manifest\n"
        "MANIFEST = Manifest(key='forward', display_name='forward', version='9.9.9', "
        "requires_platform_capabilities=[])\n",
        encoding="utf-8",
    )
    (repo / "forward" / "__init__.py").write_text("PLUGIN_CLASS = None\n", encoding="utf-8")
    (repo / "forward" / "plugin.py").write_text("class X: pass\n", encoding="utf-8")

    class _DB:
        async def get(self, *_args, **_kwargs):  # noqa: ARG002
            return None

    row = SimpleNamespace(url="https://github.com/example/plugins.git")
    with pytest.raises(svc.DuplicatePluginName) as ex:
        await svc._install_plugin_from_cached_repo(
            _DB(),
            row,
            repo,
            "forward",
            default_enabled=False,
            replace_existing=False,
            triggered_by_user_id=None,
        )

    assert ex.value.code == "KEY_CONFLICTS_BUILTIN"


@pytest.mark.asyncio
async def test_install_plugin_from_repo_preserves_outward_symlinks(tmp_path, monkeypatch) -> None:
    """copytree(symlinks=True)：插件目录里指向宿主路径的 symlink 保留为链接本身，不复制目标内容。"""
    import os

    repo = tmp_path / "repo"
    _write_repo_plugin(repo, "symdemo")
    # 安装阶段只解析 plugin.json；补上平台能力声明让校验通过
    plugin_json = json.loads((repo / "symdemo" / "plugin.json").read_text(encoding="utf-8"))
    plugin_json["requires_platform_capabilities"] = []
    (repo / "symdemo" / "plugin.json").write_text(
        json.dumps(plugin_json), encoding="utf-8"
    )
    # 补齐运行期结构，让安装校验通过
    (repo / "symdemo" / "manifest.py").write_text(
        "from app.worker.plugins.manifest import Manifest\n"
        "MANIFEST = Manifest(key='symdemo', display_name='symdemo', version='1.0.0', "
        "requires_platform_capabilities=[])\n",
        encoding="utf-8",
    )
    (repo / "symdemo" / "__init__.py").write_text("PLUGIN_CLASS = None\n", encoding="utf-8")
    (repo / "symdemo" / "plugin.py").write_text("class X: pass\n", encoding="utf-8")

    # 模拟 Git 仓库携带的外指 symlink：指向插件目录之外的宿主路径
    host_secret = tmp_path / "host_secret"
    host_secret.mkdir()
    (host_secret / "secret.txt").write_text("host-secret-content", encoding="utf-8")
    (repo / "symdemo" / "outsider").symlink_to(host_secret, target_is_directory=True)

    installed_root = tmp_path / "installed"
    monkeypatch.setattr(svc, "_plugin_dir", lambda name: installed_root / name)

    class _Result:
        def scalar_one_or_none(self):
            return None

        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class _DB:
        def __init__(self) -> None:
            self.added: list = []

        async def get(self, *_args, **_kwargs):
            return None

        def add(self, obj) -> None:
            self.added.append(obj)

        async def flush(self) -> None:
            return None

        async def execute(self, _stmt):
            return _Result()

    view = await svc._install_plugin_from_cached_repo(
        _DB(),
        SimpleNamespace(url="https://github.com/example/plugins.git"),
        repo,
        "symdemo",
        default_enabled=False,
        replace_existing=False,
        triggered_by_user_id=None,
    )

    assert view.name == "symdemo"
    target = installed_root / "symdemo"
    outsider = target / "outsider"
    # 链接本身被保留，而不是把宿主目录内容复制成普通目录
    assert outsider.is_symlink()
    assert os.readlink(str(outsider)) == str(host_secret)
    # 目标内容没有以普通文件形式落进插件目录
    assert not (target / "secret.txt").exists()


@pytest.mark.asyncio
async def test_force_refresh_cached_repo_surfaces_git_failure(tmp_path, monkeypatch) -> None:
    url = "https://github.com/example/plugins.git"
    target = tmp_path / "cache" / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(svc, "_cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(svc, "_cache_dir_for", lambda _url: target)

    async def _run_git_fail(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(svc, "_run_git", _run_git_fail)

    with pytest.raises(RuntimeError, match="network down"):
        await svc._ensure_repo_cached(url, force_refresh=True)


@pytest.mark.asyncio
async def test_non_forced_cached_repo_keeps_old_copy_on_git_failure(tmp_path, monkeypatch) -> None:
    url = "https://github.com/example/plugins.git"
    target = tmp_path / "cache" / "repo"
    (target / ".git").mkdir(parents=True)
    (target / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(svc, "_cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(svc, "_cache_dir_for", lambda _url: target)

    async def _run_git_fail(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(svc, "_run_git", _run_git_fail)

    assert await svc._ensure_repo_cached(url, force_refresh=False) == target


def test_github_token_uses_extraheader_env_without_url_mutation() -> None:
    env = svc._github_token_env("https://github.com/example/private-plugins.git", "ghp_secret123")

    assert env is not None
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    encoded = env["GIT_CONFIG_VALUE_0"].removeprefix("Authorization: Basic ")
    assert b64decode(encoded.encode()).decode() == "x-access-token:ghp_secret123"
    assert "github.com/example/private-plugins.git" not in str(env.values())
    assert "ghp_secret123" not in str(env.values())


def test_github_token_only_applies_to_github_https() -> None:
    with pytest.raises(svc.InvalidPluginRepoCredential):
        svc._github_token_env("https://gitlab.com/example/private.git", "ghp_secret123")
    with pytest.raises(svc.InvalidPluginRepoCredential):
        svc._github_token_env("git@github.com:example/private.git", "ghp_secret123")


@pytest.mark.asyncio
async def test_ensure_repo_cached_passes_private_github_env(tmp_path, monkeypatch) -> None:
    url = "https://github.com/example/private-plugins.git"
    target = tmp_path / "cache" / "repo"
    calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    monkeypatch.setattr(svc, "_cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(svc, "_cache_dir_for", lambda _url: target)

    async def _run_git_capture(*args, **kwargs):
        calls.append((tuple(args), kwargs.get("env")))
        target.mkdir(parents=True, exist_ok=True)
        (target / ".git").mkdir(exist_ok=True)
        return ""

    monkeypatch.setattr(svc, "_run_git", _run_git_capture)

    assert await svc._ensure_repo_cached(url, token="ghp_private123") == target
    assert calls[0][0] == ("clone", "--depth", "1", url, str(target))
    assert calls[0][1]["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert "ghp_private123" not in calls[0][1]["GIT_CONFIG_VALUE_0"]


@pytest.mark.asyncio
async def test_ensure_repo_cached_supports_github_tree_branch_url(tmp_path, monkeypatch) -> None:
    url = "https://github.com/example/private-plugins/tree/feature/test-branch"
    target = tmp_path / "cache" / "repo"
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(svc, "_cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(svc, "_cache_dir_for", lambda _url: target)

    async def _run_git_capture(*args, **_kwargs):
        calls.append(tuple(args))
        target.mkdir(parents=True, exist_ok=True)
        (target / ".git").mkdir(exist_ok=True)
        return ""

    monkeypatch.setattr(svc, "_run_git", _run_git_capture)

    assert await svc._ensure_repo_cached(url, token="ghp_private123") == target
    assert calls[0] == (
        "clone",
        "--depth",
        "1",
        "--branch",
        "feature/test-branch",
        "--single-branch",
        "https://github.com/example/private-plugins.git",
        str(target),
    )


@pytest.mark.asyncio
async def test_ensure_repo_cached_refreshes_github_tree_branch_cache(tmp_path, monkeypatch) -> None:
    url = "https://github.com/example/plugins/tree/codex-image-test"
    target = tmp_path / "cache" / "repo"
    calls: list[tuple[str, ...]] = []
    (target / ".git").mkdir(parents=True)
    (target / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(svc, "_cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(svc, "_cache_dir_for", lambda _url: target)

    async def _run_git_capture(*args, **_kwargs):
        calls.append(tuple(args))
        return ""

    monkeypatch.setattr(svc, "_run_git", _run_git_capture)

    assert await svc._ensure_repo_cached(url, force_refresh=True) == target
    assert calls == [
        (
            "fetch",
            "--depth",
            "1",
            "--prune",
            "origin",
            "+refs/heads/codex-image-test:refs/remotes/origin/codex-image-test",
        ),
        ("rev-parse", "--verify", "refs/remotes/origin/codex-image-test"),
        ("reset", "--hard", "refs/remotes/origin/codex-image-test"),
        ("clean", "-fd"),
    ]


@pytest.mark.asyncio
async def test_ensure_repo_cached_serializes_git_for_same_cache(tmp_path, monkeypatch) -> None:
    url = "https://github.com/example/plugins/tree/main"
    target = tmp_path / "cache" / "repo"
    (target / ".git").mkdir(parents=True)
    active = 0
    max_active = 0

    monkeypatch.setattr(svc, "_cache_dir_for", lambda _url: target)
    svc._REPO_CACHE_LOCKS.clear()

    async def _run_git_serialized(*_args, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "refs/remotes/origin/main"

    monkeypatch.setattr(svc, "_run_git", _run_git_serialized)

    await asyncio.gather(
        svc._ensure_repo_cached(url, force_refresh=True),
        svc._ensure_repo_cached(url, force_refresh=True),
    )

    assert max_active == 1


@pytest.mark.asyncio
async def test_repo_lock_covers_cache_consumer_until_scan_finishes(tmp_path, monkeypatch) -> None:
    url = "https://github.com/example/plugins/tree/main"
    row = SimpleNamespace(url=url)
    active = 0
    max_active = 0

    svc._REPO_CACHE_LOCKS.clear()
    monkeypatch.setattr(svc, "_cache_dir_for", lambda _url: tmp_path / "cache")
    monkeypatch.setattr(svc, "_get_repo", lambda _db, _repo_id: asyncio.sleep(0, result=row))
    monkeypatch.setattr(svc, "_repo_credential", lambda _row: None)
    monkeypatch.setattr(
        svc,
        "_ensure_repo_cached_unlocked",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=tmp_path / "cache"),
    )

    async def _consume(_db, _row, _repo_dir):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return []

    monkeypatch.setattr(svc, "_list_plugins_in_cached_repo", _consume)

    await asyncio.gather(
        svc.list_plugins_in_repo(object(), 1, force_refresh=True),
        svc.list_plugins_in_repo(object(), 1, force_refresh=True),
    )

    assert max_active == 1


@pytest.mark.asyncio
async def test_repo_listing_marks_same_name_from_other_source_for_migration(tmp_path) -> None:
    repo = tmp_path / "repo"
    _write_repo_plugin(repo, "ai_chat", version="2.0.0")
    row = SimpleNamespace(url="https://github.com/example/new-plugins.git")

    class _InstalledRows:
        def all(self):
            return [
                (
                    "ai_chat",
                    "1.0.0",
                    "repo",
                    "https://github.com/example/deleted-plugins/tree/0.33.x",
                )
            ]

    class _DB:
        async def execute(self, _stmt):
            return _InstalledRows()

    plugins = await svc._list_plugins_in_cached_repo(_DB(), row, repo)

    assert len(plugins) == 1
    assert plugins[0].installed is True
    assert plugins[0].installed_version == "1.0.0"
    assert plugins[0].installed_source == "repo"
    assert plugins[0].source_matches is False
    assert plugins[0].update_available is False


def test_same_repo_source_compares_branch_as_part_of_source() -> None:
    assert svc._same_repo_source(
        "https://github.com/example/plugins/tree/main",
        "https://github.com/example/plugins.git/tree/main",
    )
    assert not svc._same_repo_source(
        "https://github.com/example/plugins/tree/0.33.x",
        "https://github.com/example/plugins.git",
    )


@pytest.mark.asyncio
async def test_run_git_redacts_private_token_in_errors(monkeypatch) -> None:
    class _FakeProc:
        returncode = 128

        async def communicate(self):
            return (
                b"",
                b"fatal: Authentication failed for https://x-access-token:ghp_secret123@github.com/private/repo.git",
            )

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(svc.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr("app.services.remote_plugin_service.asyncio.create_subprocess_exec", _fake_exec)

    with pytest.raises(GitOperationFailed) as ex:
        await svc._run_git("clone", "https://x-access-token:ghp_secret123@github.com/private/repo.git")

    assert "ghp_secret123" not in ex.value.message
    assert "***:***@github.com" in ex.value.message


@pytest.mark.asyncio
async def test_create_repo_encrypts_github_token(monkeypatch) -> None:
    stored: list[object] = []

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _DB:
        async def execute(self, _stmt):
            return _Result()

        def add(self, row):
            stored.append(row)

        async def flush(self):
            return None

    monkeypatch.setattr(svc, "encrypt_str", lambda value: f"enc:{value}")

    row = await svc.create_repo(
        _DB(),
        "https://github.com/example/private-plugins.git",
        name="private",
        auth_type="github_token",
        credential="ghp_private123",
    )

    assert row.auth_type == "github_token"
    assert row.credential_enc == "enc:ghp_private123"
    assert row.has_credentials is True
    assert stored == [row]


@pytest.mark.asyncio
async def test_update_repo_credential_can_clear(monkeypatch) -> None:
    repo = SimpleNamespace(
        id=1,
        url="https://github.com/example/private-plugins.git",
        auth_type="github_token",
        credential_enc="enc:old",
    )

    class _Result:
        def scalar_one_or_none(self):
            return repo

    class _DB:
        async def execute(self, _stmt):
            return _Result()

        async def flush(self):
            return None

    row = await svc.update_repo_credential(_DB(), 1, auth_type="none", token=None)

    assert row.auth_type == "none"
    assert row.credential_enc is None


@pytest.mark.asyncio
async def test_update_repo_credential_empty_token_clears_even_with_default_auth_type() -> None:
    repo = SimpleNamespace(
        id=1,
        url="https://github.com/example/private-plugins.git",
        auth_type="github_token",
        credential_enc="enc:old",
    )

    class _Result:
        def scalar_one_or_none(self):
            return repo

    class _DB:
        async def execute(self, _stmt):
            return _Result()

        async def flush(self):
            return None

    row = await svc.update_repo_credential(_DB(), 1, auth_type="github_token", token="")

    assert row.auth_type == "none"
    assert row.credential_enc is None


@pytest.mark.asyncio
async def test_create_repo_explicit_github_auth_requires_token() -> None:
    class _Result:
        def scalar_one_or_none(self):
            return None

    class _DB:
        async def execute(self, _stmt):
            return _Result()

    with pytest.raises(svc.InvalidPluginRepoCredential):
        await svc.create_repo(
            _DB(),
            "https://github.com/example/private-plugins.git",
            auth_type="github_token",
            credential=None,
        )
