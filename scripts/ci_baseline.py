#!/usr/bin/env python3
"""Print the latest successful push ancestor; no reliable baseline means full CI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def choose_baseline(runs: list[dict], branch: str, head: str, root: Path) -> str:
    for run in runs:
        if not isinstance(run, dict):
            continue
        sha = run.get("head_sha", "")
        if (
            run.get("conclusion") != "success"
            or run.get("event") != "push"
            or run.get("head_branch") != branch
            or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", sha)
        ):
            continue
        if sha == head:
            continue
        if (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, head], cwd=root, capture_output=True, check=False
            ).returncode
            == 0
        ):
            return sha
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True, choices=["ci.yml", "ios.yml"])
    parser.add_argument("--branch", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    baseline = ""
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        response = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/actions/workflows/{args.workflow}/runs",
                "--method",
                "GET",
                "-f",
                f"branch={args.branch}",
                "-f",
                "event=push",
                "-f",
                "status=success",
                "-f",
                "per_page=30",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        baseline = choose_baseline(
            json.loads(response.stdout)["workflow_runs"], args.branch, args.head, args.root
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    if not baseline:
        print("无法找到可靠的最近全绿祖先，本次使用全量检查。", file=sys.stderr)
    print(baseline)


if __name__ == "__main__":
    main()
