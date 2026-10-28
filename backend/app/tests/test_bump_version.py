"""scripts/bump-version.py 与 scripts/check-version-sync.py：一条命令升版本、版本源减少后仍一致。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUMP = REPO_ROOT / "scripts" / "bump-version.py"
CHECK = REPO_ROOT / "scripts" / "check-version-sync.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bump = _load(BUMP, "telepilot_bump_version")


@pytest.mark.parametrize(
    ("start", "level", "expected"),
    [
        ("0.106.0-beta.17", "patch", "0.106.0-beta.18"),
        ("0.106.0", "patch", "0.106.1-beta.1"),
        ("0.106.0-beta.17", "minor", "0.107.0-beta.1"),
        ("0.106.2-beta.3", "minor", "0.107.0-beta.1"),
        ("0.106.0-beta.17", "major", "1.0.0-beta.1"),
        ("0.106.0-beta.17", "stable", "0.106.0"),
    ],
)
def test_next_version_follows_agents_rules(start: str, level: str, expected: str) -> None:
    assert str(bump.next_version(bump.Version.parse(start), level)) == expected


def test_semver_ordering_puts_prerelease_before_stable() -> None:
    versions = ["0.106.0", "0.106.0-beta.17", "0.106.0-beta.9", "0.99.1", "0.106.1-beta.1"]
    ordered = sorted((bump.Version.parse(v) for v in versions), key=bump.Version.key)
    assert [str(v) for v in ordered] == [
        "0.99.1",
        "0.106.0-beta.9",
        "0.106.0-beta.17",
        "0.106.0",
        "0.106.1-beta.1",
    ]


OPENAPI = {
    "components": {"schemas": {"VersionInfo": {"properties": {"version": {"type": "string"}}}}},
    "info": {"title": "TelePilot", "version": "0.106.0-beta.17"},
    "openapi": "3.1.0",
    "paths": {},
}
CHANGELOG = """# Changelog

说明。

---

## [Unreleased]

### CI

- 发版提速。

## [0.106.0-beta.17] - 2026-10-28 · patch（补丁预发布） · 上一版

- 旧内容。
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _write_sources(repo: Path, version: str, changelog: str) -> None:
    (repo / "backend/app").mkdir(parents=True, exist_ok=True)
    (repo / "frontend/src/lib").mkdir(parents=True, exist_ok=True)
    (repo / "openapi").mkdir(exist_ok=True)
    (repo / "backend/app/__init__.py").write_text(f'"""doc"""\n__version__ = "{version}"\nAPP_STAGE = None\n')
    (repo / "backend/pyproject.toml").write_text(
        '[project]\nname = "telepilot-backend"\ndynamic = ["version"]\ndependencies = []\n\n'
        '[tool.setuptools.dynamic]\nversion = {attr = "app.__version__"}\n'
    )
    (repo / "frontend/package.json").write_text(
        json.dumps({"name": "telepilot-frontend", "private": True, "version": version, "type": "module"}, indent=2)
        + "\n"
    )
    (repo / "frontend/src/lib/version.ts").write_text(
        'import packageJson from "../../package.json" with { type: "json" };\n'
        "export const APP_VERSION: string = packageJson.version;\n"
    )
    openapi = json.loads(json.dumps(OPENAPI))
    openapi["info"]["version"] = version
    (repo / "openapi/telepilot.openapi.json").write_text(
        json.dumps(openapi, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    (repo / "CHANGELOG.md").write_text(changelog)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(BUMP, repo / "scripts" / "bump-version.py")
    shutil.copy(CHECK, repo / "scripts" / "check-version-sync.py")
    _git(repo, "init", "-q", "-b", "Beta")
    _git(repo, "config", "user.name", "TelePilot CI")
    _git(repo, "config", "user.email", "ci@telepilot.invalid")
    _write_sources(repo, "0.106.0-beta.17", CHANGELOG.replace("### CI\n\n- 发版提速。\n\n", ""))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "beta.17")
    _git(repo, "branch", "main")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "CHANGELOG.md").write_text(CHANGELOG)
    return repo


def _run_bump(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/bump-version.py", "--no-fetch", "--base-ref", "Beta", "--main-ref", "main",
         "--date", "2026-10-29", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _versions(repo: Path) -> set[str]:
    check = _load(repo / "scripts" / "check-version-sync.py", "telepilot_check_version_sync_tmp")
    return set(check.read_versions(repo).values())


def test_bump_writes_all_sources_and_moves_unreleased(repo: Path) -> None:
    result = _run_bump(repo, "--title", "发版提速")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0.106.0-beta.17 → 0.106.0-beta.18" in result.stdout
    assert "version sync ok: 0.106.0-beta.18" in result.stdout
    assert _versions(repo) == {"0.106.0-beta.18"}
    openapi = json.loads((repo / "openapi/telepilot.openapi.json").read_text())
    assert openapi["components"] == OPENAPI["components"]
    changelog = (repo / "CHANGELOG.md").read_text()
    assert (
        "## [Unreleased]\n\n## [0.106.0-beta.18] - 2026-10-29 · patch（补丁预发布） · 发版提速\n\n### CI\n\n- 发版提速。\n\n"
        "## [0.106.0-beta.17]"
    ) in changelog


def test_bump_refuses_empty_unreleased(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text(CHANGELOG.replace("### CI\n\n- 发版提速。\n\n", ""))
    result = _run_bump(repo, "--title", "空")
    assert result.returncode != 0
    assert "Unreleased 为空" in result.stderr


def test_rerun_after_rebase_moves_pending_section_to_next_free_version(repo: Path) -> None:
    assert _run_bump(repo, "--title", "发版提速").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature beta.18")

    # 另一个 PR 先占用 beta.18 合并进 Beta。
    _git(repo, "checkout", "-q", "Beta")
    other = CHANGELOG.replace(
        "### CI\n\n- 发版提速。\n\n",
        "",
    ).replace(
        "## [0.106.0-beta.17]",
        "## [0.106.0-beta.18] - 2026-10-29 · patch（补丁预发布） · 别的 PR\n\n- 别的内容。\n\n## [0.106.0-beta.17]",
    )
    _write_sources(repo, "0.106.0-beta.18", other)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "other beta.18")

    # 模拟 rebase 后解决冲突：版本文件取目标分支，CHANGELOG 两段都保留（两段同为 beta.18，本分支段在下方）。
    _git(repo, "checkout", "-q", "feature")
    merged = other.replace(
        "## [0.106.0-beta.17]",
        "## [0.106.0-beta.18] - 2026-10-29 · patch（补丁预发布） · 发版提速\n\n### CI\n\n- 发版提速。\n\n"
        "## [0.106.0-beta.17]",
    )
    _write_sources(repo, "0.106.0-beta.18", merged)

    result = _run_bump(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "本分支未合并的版本段落：0.106.0-beta.18（将顺延为 0.106.0-beta.19）" in result.stdout
    assert _versions(repo) == {"0.106.0-beta.19"}
    changelog = (repo / "CHANGELOG.md").read_text()
    assert changelog.count("## [0.106.0-beta.18]") == 1
    assert "## [0.106.0-beta.18] - 2026-10-29 · patch（补丁预发布） · 别的 PR" in changelog
    assert changelog.index("## [0.106.0-beta.19] - 2026-10-29 · patch（补丁预发布） · 发版提速") < changelog.index(
        "## [0.106.0-beta.18]"
    )


def test_rerun_renames_unmerged_section_when_target_moved(repo: Path) -> None:
    assert _run_bump(repo, "--title", "发版提速").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature beta.18")
    # 目标分支前进到 beta.19（别的 PR 连续合并两次），本分支的 beta.18 段落尚未合并。
    _git(repo, "checkout", "-q", "Beta")
    other = CHANGELOG.replace("### CI\n\n- 发版提速。\n\n", "").replace(
        "## [0.106.0-beta.17]",
        "## [0.106.0-beta.19] - 2026-10-29 · patch（补丁预发布） · B\n\n- b\n\n"
        "## [0.106.0-beta.17]",
    )
    _write_sources(repo, "0.106.0-beta.19", other)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "other beta.19")
    _git(repo, "checkout", "-q", "feature")
    # 冲突解决：版本文件取目标分支，CHANGELOG 保留两段（本分支段在下方）。
    merged = other.replace(
        "## [0.106.0-beta.17]",
        "## [0.106.0-beta.18] - 2026-10-29 · patch（补丁预发布） · 发版提速\n\n### CI\n\n- 发版提速。\n\n"
        "## [0.106.0-beta.17]",
    )
    _write_sources(repo, "0.106.0-beta.19", merged)

    result = _run_bump(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "本分支未合并的版本段落：0.106.0-beta.18（将顺延为 0.106.0-beta.20）" in result.stdout
    assert _versions(repo) == {"0.106.0-beta.20"}
    changelog = (repo / "CHANGELOG.md").read_text()
    assert "## [0.106.0-beta.18]" not in changelog
    assert changelog.index("## [0.106.0-beta.20] - 2026-10-29 · patch（补丁预发布） · 发版提速") < changelog.index(
        "## [0.106.0-beta.19]"
    )


def test_minor_and_stable_levels_write_matching_labels(repo: Path) -> None:
    result = _run_bump(repo, "--level", "minor", "--title", "新阶段")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _versions(repo) == {"0.107.0-beta.1"}
    assert "## [0.107.0-beta.1] - 2026-10-29 · minor（次版本预发布） · 新阶段" in (repo / "CHANGELOG.md").read_text()


def test_conflict_markers_block_bump(repo: Path) -> None:
    path = repo / "backend/app/__init__.py"
    path.write_text(path.read_text() + "<<<<<<< HEAD\n=======\n>>>>>>> x\n")
    result = _run_bump(repo, "--title", "x")
    assert result.returncode != 0
    assert "冲突标记" in result.stderr


def test_repository_versions_are_in_sync() -> None:
    check = _load(CHECK, "telepilot_check_version_sync")
    version = check.check(REPO_ROOT)
    assert version
    assert set(check.read_versions(REPO_ROOT).values()) == {version}


def test_ci_version_gate_uses_shared_check_script() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python scripts/check-version-sync.py" in workflow
