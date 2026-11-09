#!/usr/bin/env python3
"""Run the selected suite using argument arrays; invalid plans run the full suite."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command_for(
    target: str, selection: dict, *, suite: str = "visual", project: str = "desktop", root: Path = ROOT
):
    tests = selection.get("tests") if selection.get("mode") == "selected" else None
    prefix = "backend/app/tests/" if target == "backend" else "frontend/tests/"
    if tests is not None and (
        not isinstance(tests, list)
        or not tests
        or any(
            not isinstance(p, str)
            or not p.startswith(prefix)
            or ".." in Path(p).parts
            or not (root / p).is_file()
            for p in tests
        )
        or (
            target == "backend"
            and any(not Path(p).name.startswith("test_") or not p.endswith(".py") for p in tests)
        )
    ):
        tests = None
    if target == "backend":
        return [
            sys.executable,
            "-m",
            "pytest",
            *([str(Path(p).relative_to("backend")) for p in tests] if tests else []),
        ], root / "backend"
    if suite not in {"a11y", "visual"} or project not in {"mobile", "tablet", "desktop"}:
        raise ValueError("Unknown browser suite or project")
    selected = [
        str(Path(p).relative_to("frontend"))
        for p in tests or []
        if p.startswith(f"frontend/tests/{suite}/") and p.endswith(".spec.ts")
    ]
    return [
        "pnpm",
        "exec",
        "playwright",
        "test",
        *(selected or [f"tests/{suite}"]),
        f"--project={project}",
        "--workers=1",
        f"--output=test-results/{suite}",
    ], root / "frontend"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["backend", "browser"])
    parser.add_argument("--suite", choices=["a11y", "visual"], default="visual")
    parser.add_argument("--project", choices=["mobile", "tablet", "desktop"], default="desktop")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--prepare", type=Path, help="Compute one browser plan for both suites in this job")
    args = parser.parse_args()
    plan = {}
    try:
        plan = json.loads(os.environ.get("CI_TEST_PLAN", "{}"))
        selection = plan.get(args.target, {})
        if args.target == "browser":
            if cached_path := (os.environ.get("CI_BROWSER_PLAN_FILE") if not args.prepare else None):
                cached = json.loads(Path(cached_path).read_text())
                if cached.get("context") != plan.get("context"):
                    raise ValueError("Cached selection belongs to another change set")
                selection = cached["selection"]
            else:
                result = subprocess.run(
                    ["node", str(ROOT / "scripts/ci_frontend_impact.cjs")],
                    input=json.dumps(plan.get("context")),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                selection = json.loads(result.stdout)
        if not isinstance(selection, dict):
            raise TypeError("Invalid test plan")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError):
        selection = {"mode": "full", "tests": None, "reason": "测试选择无法可靠解析"}
    if args.prepare:
        args.prepare.write_text(
            json.dumps(
                {"context": plan.get("context") if isinstance(plan, dict) else None, "selection": selection},
                ensure_ascii=False,
            )
        )
        print(f"Browser selection: {selection.get('mode', 'full')}, {selection.get('reason', '')}")
        return
    command, cwd = command_for(args.target, selection, suite=args.suite, project=args.project)
    if (
        (args.target == "backend" and len(command) == 3)
        or (args.target == "browser" and command[4:5] == [f"tests/{args.suite}"])
    ) and selection.get("mode") != "full":
        selection = {"mode": "full", "tests": None, "reason": "本任务没有有效的非空选择，执行完整测试"}
    if args.list:
        command += ["--collect-only", "-q"] if args.target == "backend" else ["--list", "--reporter=json"]
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False))
        return
    if not args.list:
        print(
            f"{args.target}: {selection.get('mode', 'full')} — {selection.get('reason', '无可靠计划，运行全量')}",
            flush=True,
        )
        if selection.get("tests"):
            print("\n".join(selection["tests"]), flush=True)
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary).open("a") as handle:
                handle.write(f"\n### {args.target} 实际选择（{args.suite}/{args.project}）\n\n")
                handle.write(f"{selection.get('mode', 'full')}：{selection.get('reason', '')}\n\n")
                handle.write("```json\n" + json.dumps(command, ensure_ascii=False, indent=2) + "\n```\n")
    env = {**os.environ, **({"START_SERVER": "1"} if args.target == "browser" else {})}
    raise SystemExit(subprocess.run(command, cwd=cwd, env=env, check=False).returncode)


if __name__ == "__main__":
    main()
