"""在线更新前的工作区处理、健康探测与失败摘要。

prepare-update-worktree.sh 用真实临时 Git 仓库验证行为；健康探测用假的
docker 可执行文件验证命令拼装；updater 失败摘要直接驱动 _run_apply_job。
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PREPARE = REPO_ROOT / "scripts" / "prepare-update-worktree.sh"
LIB = REPO_ROOT / "scripts" / "_lib.sh"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_ENV, **(env or {})},
    )
    return result.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "telepilot"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.fileMode", "true")
    (repo / ".gitignore").write_text(".env\nplugins/installed/*\n", encoding="utf-8")
    (repo / "run.sh").write_text("echo run\n", encoding="utf-8")
    (repo / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def _prepare(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # 刻意不带 git 身份：updater 容器里通常没有，脚本必须自带 stash 作者信息。
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    clean_env.update({"HOME": str(repo.parent), "GIT_CONFIG_NOSYSTEM": "1"})
    clean_env.update(env or {})
    return subprocess.run(
        ["bash", str(PREPARE), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=clean_env,
    )


def test_clean_worktree_passes_without_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _prepare(repo, "abc")

    assert result.returncode == 0
    assert "工作区干净" in result.stdout
    assert _git(repo, "stash", "list") == ""


def test_ignored_runtime_files_never_count_as_dirty(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (repo / "plugins" / "installed").mkdir(parents=True)
    (repo / "plugins" / "installed" / "x.py").write_text("x\n", encoding="utf-8")

    result = _prepare(repo)

    assert result.returncode == 0
    assert (repo / ".env").read_text(encoding="utf-8") == "SECRET=1\n"
    assert _git(repo, "stash", "list") == ""


def test_mode_only_changes_set_repository_filemode_instead_of_stash(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    script = repo / "run.sh"
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    dry = _prepare(repo, "--check-only")
    assert dry.returncode == 0
    assert "只有文件可执行位差异" in dry.stderr
    assert _git(repo, "config", "core.fileMode").strip() == "true"

    result = _prepare(repo, "0123456789abcdef")

    assert result.returncode == 0, result.stderr
    assert _git(repo, "config", "--local", "core.fileMode").strip() == "false"
    assert "core.fileMode=false" in result.stderr
    assert "git config --unset core.fileMode" in result.stderr
    assert _git(repo, "stash", "list") == ""
    assert _git(repo, "status", "--porcelain") == ""


def test_content_and_untracked_changes_are_auto_stashed_with_recovery_commands(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    old_commit = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "app.py").write_text("print('local edit')\n", encoding="utf-8")
    (repo / "备注.txt").write_text("note\n", encoding="utf-8")
    (repo / ".env").write_text("KEEP=1\n", encoding="utf-8")

    dry = _prepare(repo, "--check-only", old_commit)
    assert dry.returncode == 0
    assert " M app.py" in dry.stderr
    assert "?? 备注.txt" in dry.stderr
    assert _git(repo, "stash", "list") == ""

    result = _prepare(repo, old_commit, env={"TELEPILOT_HOST_PROJECT_DIR": "/opt/telepilot"})

    assert result.returncode == 0, result.stderr
    assert "共 2 项" in result.stderr
    stash_list = _git(repo, "stash", "list")
    assert f"telepilot-auto-stash {old_commit[:12]} " in stash_list
    assert "git stash list" in result.stderr
    assert "git stash pop" in result.stderr
    assert "/opt/telepilot" in result.stderr
    assert _git(repo, "status", "--porcelain") == ""
    # 被忽略的 .env 不进 stash、不被删除；暂存内容可完整找回。
    assert (repo / ".env").read_text(encoding="utf-8") == "KEEP=1\n"
    _git(repo, "stash", "pop")
    assert (repo / "app.py").read_text(encoding="utf-8") == "print('local edit')\n"
    assert (repo / "备注.txt").exists()


def test_merge_in_progress_refuses_and_lists_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("print('half resolved')\n", encoding="utf-8")
    merge_head = repo / ".git" / "MERGE_HEAD"
    merge_head.write_text(_git(repo, "rev-parse", "HEAD"), encoding="utf-8")

    result = _prepare(repo)

    assert result.returncode == 1
    assert " M app.py" in result.stderr
    assert "MERGE_HEAD" in result.stderr
    assert "✗ 工作区存在未提交改动，拒绝自动更新" in result.stderr
    assert "（1 项：app.py）" in result.stderr
    assert _git(repo, "stash", "list") == ""


def test_unmerged_conflict_refuses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-qb", "other")
    (repo / "app.py").write_text("print('other')\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "other")
    _git(repo, "checkout", "-q", "-")
    (repo / "app.py").write_text("print('main')\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "main")
    subprocess.run(
        ["git", "merge", "other"],
        cwd=repo,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
        check=False,
    )
    (repo / ".git" / "MERGE_HEAD").unlink()

    result = _prepare(repo)

    assert result.returncode == 1
    assert "未解决的合并冲突" in result.stderr


def test_large_untracked_files_are_not_stashed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "dump.sql").write_bytes(b"x" * 4096)

    result = _prepare(repo, env={"TELEPILOT_AUTO_STASH_MAX_UNTRACKED_BYTES": "1024"})

    assert result.returncode == 1
    assert "?? dump.sql" in result.stderr
    assert ".git/info/exclude" in result.stderr
    assert _git(repo, "stash", "list") == ""


def test_dirty_list_is_capped_with_total_count(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for index in range(35):
        (repo / f"extra-{index:02d}.txt").write_text("x\n", encoding="utf-8")
    (repo / ".git" / "MERGE_HEAD").write_text(_git(repo, "rev-parse", "HEAD"), encoding="utf-8")

    result = _prepare(repo)

    assert result.returncode == 1
    assert "共 35 项" in result.stderr
    assert "... 还有 5 项未列出" in result.stderr
    assert result.stderr.count("?? extra-") == 30
    assert "35 项：extra-00.txt，" in result.stderr


def test_prod_update_prepares_worktree_before_fast_forward() -> None:
    script = (REPO_ROOT / "scripts" / "prod-update.sh").read_text(encoding="utf-8")

    prepare = script.index('bash "$SCRIPT_DIR/prepare-update-worktree.sh" "$CHECKED_OUT_COMMIT" || exit 1')
    dry_run = script.index('prepare-update-worktree.sh" --check-only "$CHECKED_OUT_COMMIT" || true')
    fast_forward = script.index('git merge --ff-only "$TARGET_COMMIT"')
    assert dry_run < prepare < fast_forward
    assert "git status --porcelain)\" ]]; then" not in script
    assert 'git pull --ff-only "$REMOTE" "$BRANCH"' not in script


def _fake_docker(tmp_path: Path, *, health_json: str, ps_json: str, exec_rc: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'if [[ "$1" == "inspect" ]]; then\n'
        f"  printf '%s\\n' '{health_json}'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "compose" && " $* " == *" -q "* ]]; then echo cid123; exit 0; fi\n'
        f"if [[ \"$1\" == \"compose\" ]]; then printf '%s\\n' '{ps_json}'; exit 0; fi\n"
        f'if [[ "$1" == "exec" ]]; then exit {exec_rc}; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir


def _run_lib(tmp_path: Path, bin_dir: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "{LIB}"; {snippet}'],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        cwd=tmp_path,
    )


def test_wait_compose_healthy_probes_service_healthcheck_while_starting(tmp_path: Path) -> None:
    bin_dir = _fake_docker(
        tmp_path,
        health_json='{"Test":["CMD","python","-c","print(1)"],"Interval":15000000000}',
        ps_json='[{"Health":"starting","State":"running"}]',
        exec_rc=0,
    )

    result = _run_lib(tmp_path, bin_dir, "wait_compose_healthy docker-compose.yml web 5")

    assert result.returncode == 0, result.stderr
    assert "主动探测通过" in result.stdout
    calls = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "exec cid123 python -c print(1)" in calls


def test_compose_probe_runs_shell_healthchecks_and_rejects_missing_ones(tmp_path: Path) -> None:
    (tmp_path / "shell").mkdir()
    shell_bin = _fake_docker(
        tmp_path / "shell",
        health_json='{"Test":["CMD-SHELL","wget -qO- http://127.0.0.1/ || exit 1"]}',
        ps_json="[]",
        exec_rc=0,
    )
    shell = _run_lib(tmp_path / "shell", shell_bin, "compose_probe_health cid123")
    assert shell.returncode == 0
    calls = (tmp_path / "shell" / "docker.log").read_text(encoding="utf-8")
    assert "exec cid123 sh -c wget -qO- http://127.0.0.1/ || exit 1" in calls

    (tmp_path / "none").mkdir()
    none_bin = _fake_docker(tmp_path / "none", health_json="null", ps_json="[]", exec_rc=0)
    missing = _run_lib(tmp_path / "none", none_bin, "compose_probe_health cid123")
    assert missing.returncode == 1


def test_wait_compose_healthy_keeps_waiting_when_probe_fails(tmp_path: Path) -> None:
    bin_dir = _fake_docker(
        tmp_path,
        health_json='{"Test":["CMD","false"]}',
        ps_json='[{"Health":"starting","State":"running"}]',
        exec_rc=1,
    )

    result = _run_lib(tmp_path, bin_dir, "wait_compose_healthy docker-compose.yml web 2")

    assert result.returncode == 1
    assert "未达健康状态" in result.stderr


def _load_updater_module():
    module_path = REPO_ROOT / "deploy" / "updater" / "server.py"
    spec = importlib.util.spec_from_file_location("telepilot_updater_prepare_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failure_reason_prefers_specific_error_over_generic_trap_line() -> None:
    updater = _load_updater_module()

    assert updater._error_line_text("\x1b[0;31m✗\x1b[0m 镜像缺失") == "镜像缺失"
    assert updater._error_line_text("▸ 普通日志") is None
    assert updater._failure_reason(["镜像缺失", "增量更新失败"]) == "镜像缺失"
    assert updater._failure_reason(["增量更新失败"]) == "增量更新失败"
    assert updater._failure_reason([]) is None
    assert len(updater._failure_reason(["x" * 2000]) or "") == updater.MAX_FAILURE_REASON_CHARS


def test_apply_job_failure_detail_carries_dirty_file_summary(monkeypatch, tmp_path: Path) -> None:
    updater = _load_updater_module()
    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    (workspace / "scripts").mkdir()
    (workspace / "scripts" / "prod-update.sh").write_text(
        "echo '! 工作区未提交改动（共 2 项）：' >&2\n"
        "echo '     M backend/app/main.py' >&2\n"
        "echo '✗ 工作区存在未提交改动，拒绝自动更新：存在未解决的合并冲突"
        "（2 项：backend/app/main.py，docs/x.md）。' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(updater, "WORKSPACE", workspace)
    monkeypatch.setattr(updater, "_check_plan", lambda *_: {"ok": True, "has_update": True})
    monkeypatch.setattr(updater, "_apply_job_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(updater, "_run", lambda *_args, **_kwargs: ("", "", 1))

    updater._run_apply_job("abcdef123456", "origin", "Beta", False)

    job = updater._job_snapshot("abcdef123456")
    assert job is not None
    assert job["status"] == "failed"
    assert job["error"].startswith("prod-update 退出码 1：工作区存在未提交改动")
    assert "backend/app/main.py，docs/x.md" in job["error"]
    assert job["detail"] == job["error"]
    assert job["summary"].startswith("更新失败：工作区存在未提交改动")
    assert any("M backend/app/main.py" in line for line in job["logs"])


@pytest.mark.parametrize(
    "path",
    [
        "scripts/prod-update.sh",
        "scripts/prepare-update-worktree.sh",
        "scripts/_lib.sh",
    ],
)
def test_update_scripts_parse(path: str) -> None:
    result = subprocess.run(["bash", "-n", str(REPO_ROOT / path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
