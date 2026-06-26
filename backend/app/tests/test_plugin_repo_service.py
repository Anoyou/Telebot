from __future__ import annotations

import pytest

from app.services import plugin_repo_service as svc


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
