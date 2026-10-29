#!/usr/bin/env python3
"""主仓库 CI：用本次提交的宿主检查官方插件库（telebot-plugins）的兼容性。

流程：

1. 用当前宿主（``--host-repo``，其 ``backend`` 已装进 ``--host-python``）运行插件库的
   ``scripts/check_host_compat.py`` 与 ``pytest``；全部通过即成功。
2. 有失败时，用基线宿主（``--base-repo``，通常是 PR 的目标分支）在独立虚拟环境里重跑同样的检查，
   只把**基线上通过、当前宿主上失败**的项判为本次改动造成的不兼容；插件库自身已有的失败
   （基线上同样失败）只打印警告，不阻塞主仓库 PR。
3. 没有给 ``--base-repo`` 时，任何失败都直接判失败。

只依赖标准库。退出码：0 兼容；1 本次宿主改动导致插件不兼容；2 运行环境问题。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunResult:
    label: str
    compat_errors: dict[str, list[str]] = field(default_factory=dict)
    compat_ok: bool = True
    failed_tests: set[str] = field(default_factory=set)
    pytest_ok: bool = True
    pytest_ran: bool = False

    @property
    def ok(self) -> bool:
        return self.compat_ok and self.pytest_ok


def _env(host_repo: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["TELEPILOT_REPO"] = str(host_repo)
    env.setdefault("MASTER_KEY", "4QAy7kFzv2mkHRXTHwL_FEZABz0R3sqat5rBl4vDLXk=")
    env.setdefault("JWT_SECRET", "telepilot-plugin-compat-ci-only")
    env.pop("PYTHONPATH", None)
    return env


def _compat_errors(report_path: Path) -> dict[str, list[str]]:
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = raw if isinstance(raw, list) else raw.get("plugins") or raw.get("results") or []
    errors: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        errs = [str(err) for err in (item.get("errors") or [])]
        if errs:
            errors[str(item.get("key") or item.get("plugin") or "?")] = errs
    return errors


def _failed_tests(junit_path: Path) -> set[str]:
    try:
        tree = ET.parse(junit_path)
    except (OSError, ET.ParseError):
        return set()
    failed: set[str] = set()
    for case in tree.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(f"{case.get('classname', '')}::{case.get('name', '')}")
    return failed


def run_checks(label: str, python: str, host_repo: Path, plugins: Path, workdir: Path, report: Path | None) -> RunResult:
    result = RunResult(label=label)
    env = _env(host_repo)
    report_path = report or workdir / f"compat-{label}.json"
    print(f"::group::[{label}] 宿主兼容性检查（{host_repo}）", flush=True)
    compat = subprocess.run(
        [python, "scripts/check_host_compat.py", "--json", str(report_path)],
        cwd=plugins,
        env=env,
        check=False,
    )
    print("::endgroup::", flush=True)
    result.compat_errors = _compat_errors(report_path)
    result.compat_ok = compat.returncode == 0
    if not result.compat_ok and not result.compat_errors:
        # 脚本本身崩溃（不是插件错误）：记一个占位错误，便于与基线比较
        result.compat_errors = {"<check_host_compat>": [f"exit {compat.returncode}"]}

    junit = workdir / f"pytest-{label}.xml"
    print(f"::group::[{label}] 插件库 pytest", flush=True)
    tests = subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        cwd=plugins,
        env=env,
        check=False,
    )
    print("::endgroup::", flush=True)
    result.pytest_ran = True
    result.failed_tests = _failed_tests(junit)
    result.pytest_ok = tests.returncode == 0
    if not result.pytest_ok and not result.failed_tests:
        result.failed_tests = {f"<pytest exit {tests.returncode}>"}
    return result


def _install_base_python(base_repo: Path, workdir: Path) -> str:
    venv = workdir / "base-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = str(venv / "bin" / "python")
    subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    subprocess.run([python, "-m", "pip", "install", "-q", "-e", f"{base_repo / 'backend'}[dev]"], check=True)
    return python


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugins", required=True, type=Path, help="telebot-plugins 检出目录")
    parser.add_argument("--host-repo", required=True, type=Path, help="当前宿主（本次提交）检出目录")
    parser.add_argument("--host-python", default=sys.executable, help="已安装当前宿主 backend 的 Python")
    parser.add_argument("--base-repo", type=Path, default=None, help="基线宿主检出目录（PR 目标分支）")
    parser.add_argument("--base-python", default=None, help="已安装基线宿主 backend 的 Python（不给则自动建虚拟环境）")
    parser.add_argument("--report", type=Path, default=None, help="当前宿主的兼容性报告输出路径")
    args = parser.parse_args()

    plugins = args.plugins.resolve()
    if not (plugins / "scripts" / "check_host_compat.py").is_file():
        print(f"插件库目录无效：{plugins}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="plugin-compat-") as tmp:
        workdir = Path(tmp)
        head = run_checks("head", args.host_python, args.host_repo.resolve(), plugins, workdir, args.report)
        if head.ok:
            print("✅ 官方插件库在本次宿主上全部兼容（宿主兼容性检查与插件库测试均通过）。")
            return 0

        print("当前宿主上有失败：")
        for key, errs in head.compat_errors.items():
            print(f"  - 兼容检查 {key}: {'; '.join(errs)[:300]}")
        for test in sorted(head.failed_tests):
            print(f"  - 测试 {test}")

        if args.base_repo is None:
            print("::error::没有提供基线宿主，无法区分插件库自身的失败；按失败处理。")
            return 1

        base_python = args.base_python or _install_base_python(args.base_repo.resolve(), workdir)
        base = run_checks("base", base_python, args.base_repo.resolve(), plugins, workdir, None)

        new_compat = {key: errs for key, errs in head.compat_errors.items() if key not in base.compat_errors}
        new_tests = head.failed_tests - base.failed_tests
        pre_existing = sorted((head.failed_tests & base.failed_tests) | set(head.compat_errors) & set(base.compat_errors))
        for item in pre_existing:
            print(f"::warning::插件库自身已有的失败（基线宿主上同样失败，不归因于本次改动）：{item}")
        if new_compat or new_tests:
            for key, errs in new_compat.items():
                print(f"::error::本次宿主改动导致插件 {key} 兼容检查失败：{'; '.join(errs)[:500]}")
            for test in sorted(new_tests):
                print(f"::error::本次宿主改动导致插件库测试失败：{test}")
            return 1
        print("✅ 本次宿主改动没有引入新的插件不兼容（失败项在基线宿主上同样存在，见上方警告）。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
