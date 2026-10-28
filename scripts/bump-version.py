#!/usr/bin/env python3
"""一条命令完成 TelePilot 服务器版本 bump（规则见 AGENTS.md「版本与发布」）。

做的事：
1. ``git fetch origin --prune``，读取 ``origin/Beta``（``--base-ref``）、``origin/main`` 与当前工作树的版本；
2. 按 SemVer / beta 规则算出下一个版本号（默认 ``--level patch``：同一候选版本递增 ``beta.N``）；
3. 写入所有版本源：``backend/app/__init__.py``、``frontend/package.json``，并直接改写
   ``openapi/telepilot.openapi.json`` 的 ``info.version``（不跑完整 codegen；iOS 原生子集不带服务器版本，无需改）；
4. 把根 ``CHANGELOG.md`` 的 ``Unreleased`` 内容移到 ``## [版本] - 日期 · 级别 · 主题`` 新段落；
5. 运行 ``scripts/check-version-sync.py`` 自检（与 CI「版本同步」同一段逻辑）。

rebase 后版本被别的 PR 占用时，直接再跑一次：脚本识别出本分支尚未合并的版本段落（目标分支的
CHANGELOG 里没有的那一段），按目标分支的最新版本顺延并改名，不需要手工改文件。

用法示例（仓库根目录）：
  python3 scripts/bump-version.py --title "访客回复支持 callback 按钮"          # 0.106.0-beta.17 → beta.18
  python3 scripts/bump-version.py --level minor --title "新阶段能力"            # → 0.107.0-beta.1
  python3 scripts/bump-version.py --level stable --title "正式发布"             # 0.107.0-beta.3 → 0.107.0
  python3 scripts/bump-version.py --set 0.106.1-beta.1 --title "…"              # 显式指定
  python3 scripts/bump-version.py --dry-run --title "…"                        # 只打印计算结果
仅依赖标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = "backend/app/__init__.py"
PACKAGE = "frontend/package.json"
OPENAPI = "openapi/telepilot.openapi.json"
CHANGELOG = "CHANGELOG.md"
VERSION_FILES = (INIT, PACKAGE, OPENAPI, CHANGELOG)

INIT_RE = re.compile(r'^(__version__ = ")([^"]+)(")', re.MULTILINE)
PACKAGE_RE = re.compile(r'^(  "version": ")([^"]+)(")', re.MULTILINE)
OPENAPI_INFO_RE = re.compile(r'(\n  "info": \{\n(?:    [^\n]*\n)*?    "version": ")([^"]+)(")')
SECTION_RE = re.compile(r"^## \[([^\]]+)\][^\n]*$", re.MULTILINE)
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$")
STAGE_ORDER = {"alpha": 0, "beta": 1, "rc": 2}

LEVEL_LABELS = {
    "patch": "patch（补丁预发布）",
    "minor": "minor（次版本预发布）",
    "major": "major（主版本预发布）",
}
STABLE_LABELS = {
    "patch": "patch（补丁正式版）",
    "minor": "minor（次版本正式版）",
    "major": "major（主版本正式版）",
}


@dataclass(frozen=True, order=False)
class Version:
    major: int
    minor: int
    patch: int
    stage: str | None = None
    number: int | None = None

    @classmethod
    def parse(cls, text: str) -> Version:
        match = VERSION_RE.match(text.strip())
        if not match:
            raise SystemExit(f"无法识别的版本号：{text!r}（应为 X.Y.Z 或 X.Y.Z-beta.N）")
        major, minor, patch, stage, number = match.groups()
        return cls(int(major), int(minor), int(patch), stage, int(number) if number else None)

    @property
    def base(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def prerelease(self) -> bool:
        return self.stage is not None

    def key(self) -> tuple:
        # SemVer 排序：同一基础版本下预发布 < 正式版。
        if self.stage is None:
            return (*self.base, 1, 0, 0)
        return (*self.base, 0, STAGE_ORDER[self.stage], self.number or 0)

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.stage is not None:
            text += f"-{self.stage}.{self.number}"
        return text


def next_version(start: Version, level: str) -> Version:
    """按 AGENTS.md 规则从 start 算下一个版本。"""
    if level == "patch":
        if start.prerelease:
            # 同一候选版本继续修复和收口：只递增 beta 序号。
            return Version(*start.base, start.stage, (start.number or 0) + 1)
        # 稳定检查点之后的下一次开发：新的补丁基础版本 -beta.1。
        return Version(start.major, start.minor, start.patch + 1, "beta", 1)
    if level == "minor":
        # 新阶段能力（含 beta 期间加入 minor 级变化）：提升基础版本并从 -beta.1 重新开始。
        return Version(start.major, start.minor + 1, 0, "beta", 1)
    if level == "major":
        return Version(start.major + 1, 0, 0, "beta", 1)
    if level == "stable":
        if not start.prerelease:
            raise SystemExit(f"{start} 已是正式版，--level stable 需要从预发布版本开始")
        return Version(*start.base)
    raise SystemExit(f"未知级别：{level}")


def release_date() -> str:
    """段落日期按发布时区（默认 Asia/Shanghai，与既有 CHANGELOG 一致；TELEPILOT_RELEASE_TZ 可覆盖）。"""
    name = os.environ.get("TELEPILOT_RELEASE_TZ", "Asia/Shanghai")
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo(name)).date().isoformat()
    except Exception:  # noqa: BLE001 - 缺 tzdata 时退回本机日期
        return dt.date.today().isoformat()


def infer_level(start: Version, target: Version) -> str:
    if target.major != start.major:
        return "major"
    if target.minor != start.minor:
        return "minor"
    return "patch"


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout if result.returncode == 0 else ""


def version_at(ref: str) -> Version | None:
    text = git("show", f"{ref}:{INIT}", check=False)
    match = INIT_RE.search(text)
    return Version.parse(match.group(2)) if match else None


def changelog_sections(text: str) -> list[str]:
    return [v for v in SECTION_RE.findall(text) if v.lower() != "unreleased"]


def stable_label_for(version: Version) -> str:
    if version.patch:
        return STABLE_LABELS["patch"]
    if version.minor:
        return STABLE_LABELS["minor"]
    return STABLE_LABELS["major"]


def split_changelog(text: str) -> tuple[str, str, list[tuple[str, str]]]:
    """拆成 (Unreleased 之前的前言, Unreleased 正文, [(版本, 整段文本)...])。"""
    marker = re.search(r"^## \[Unreleased\][^\n]*\n", text, re.MULTILINE | re.IGNORECASE)
    if not marker:
        raise SystemExit("CHANGELOG.md 缺少 ## [Unreleased] 段落")
    head = text[: marker.end()]
    rest = text[marker.end():]
    starts = [m.start() for m in SECTION_RE.finditer(rest)]
    unreleased = rest[: starts[0]] if starts else rest
    sections: list[tuple[str, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(rest)
        chunk = rest[start:end]
        version = SECTION_RE.match(chunk).group(1)  # type: ignore[union-attr]
        sections.append((version, chunk))
    return head, unreleased, sections


def render_header(version: Version, date: str, label: str, title: str) -> str:
    return f"## [{version}] - {date} · {label} · {title}\n"


def rewrite_header(chunk: str, version: Version, date: str, label: str | None, title: str | None) -> str:
    first, _, body = chunk.partition("\n")
    parts = [part.strip() for part in first.split(" · ")]
    old_label = parts[1] if len(parts) > 1 else LEVEL_LABELS["patch"]
    old_title = " · ".join(parts[2:]) if len(parts) > 2 else ""
    new_title = title or old_title
    if not new_title:
        raise SystemExit("缺少版本主题：请用 --title 指定")
    return render_header(version, date, label or old_label, new_title) + body


def replace_one(pattern: re.Pattern[str], text: str, value: str, path: str) -> str:
    new_text, count = pattern.subn(lambda m: f"{m.group(1)}{value}{m.group(3)}", text, count=1)
    if count != 1:
        raise SystemExit(f"{path} 中找不到可替换的版本号")
    return new_text


def write_versions(version: Version, dry_run: bool) -> None:
    init_path = ROOT / INIT
    package_path = ROOT / PACKAGE
    openapi_path = ROOT / OPENAPI
    init_text = replace_one(INIT_RE, init_path.read_text(encoding="utf-8"), str(version), INIT)
    package_text = replace_one(PACKAGE_RE, package_path.read_text(encoding="utf-8"), str(version), PACKAGE)
    old_openapi = openapi_path.read_text(encoding="utf-8")
    openapi_text = replace_one(OPENAPI_INFO_RE, old_openapi, str(version), OPENAPI)
    # 只允许改动 info.version：其余内容解析后必须完全相同。
    before, after = json.loads(old_openapi), json.loads(openapi_text)
    if after["info"]["version"] != str(version):
        raise SystemExit("改写 openapi info.version 失败")
    after["info"]["version"] = before["info"]["version"]
    if before != after:
        raise SystemExit("改写 openapi 时意外改动了 info.version 以外的内容")
    if json.loads(package_text)["version"] != str(version):
        raise SystemExit("改写 frontend/package.json 失败")
    if dry_run:
        return
    init_path.write_text(init_text, encoding="utf-8")
    package_path.write_text(package_text, encoding="utf-8")
    openapi_path.write_text(openapi_text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="计算下一个服务器版本号并写入全部版本源与 CHANGELOG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--level",
        choices=["patch", "beta", "minor", "major", "stable"],
        default="patch",
        help="本批改动的最高级别（patch/beta：同一候选版本递增 beta.N；minor/major：提升基础版本从 beta.1 开始；"
        "stable：去掉预发布后缀）",
    )
    group.add_argument("--set", dest="explicit", help="显式指定版本号（仍校验格式与递增）")
    parser.add_argument("--title", help="版本段落主题（中文）；重新顺延已有段落时可省略以沿用原主题")
    parser.add_argument("--label", help="覆盖段落级别标签，如「minor（次版本预发布）」")
    parser.add_argument("--base-ref", default="origin/Beta", help="目标分支（默认 origin/Beta）")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--date", default=None, help="段落日期，默认 Asia/Shanghai 的今天")
    parser.add_argument("--no-fetch", action="store_true", help="不执行 git fetch（离线或测试用）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计算结果，不写文件")
    args = parser.parse_args()
    level = "patch" if args.level == "beta" else args.level

    for path in VERSION_FILES:
        if "\n<<<<<<< " in "\n" + (ROOT / path).read_text(encoding="utf-8"):
            raise SystemExit(f"{path} 仍有未解决的冲突标记；先解决冲突（版本号取目标分支即可）再运行本脚本")

    if not args.no_fetch:
        git("fetch", "origin", "--prune")

    tree_version = Version.parse(INIT_RE.search((ROOT / INIT).read_text(encoding="utf-8")).group(2))  # type: ignore[union-attr]
    base_version = version_at(args.base_ref)
    main_version = version_at(args.main_ref)
    base_changelog = git("show", f"{args.base_ref}:{CHANGELOG}", check=False)
    base_sections = set(changelog_sections(base_changelog))

    changelog_path = ROOT / CHANGELOG
    changelog_text = changelog_path.read_text(encoding="utf-8")
    head, unreleased, sections = split_changelog(changelog_text)
    # 本分支尚未合并到目标分支的版本段落（之前 bump 过、rebase 后被别的 PR 占号时需要顺延）。
    # 识别方式：段落正文不在目标分支 CHANGELOG 中，且版本号目标分支没有，或与目标分支的段落同号（rebase 撞号）。
    tree_counts: dict[str, int] = {}
    for v, _ in sections:
        tree_counts[v] = tree_counts.get(v, 0) + 1
    pending = [
        (v, chunk)
        for v, chunk in sections
        if base_changelog
        and chunk.strip() not in base_changelog
        and (v not in base_sections or tree_counts[v] > 1)
    ]
    if len(pending) > 1:
        raise SystemExit(
            "CHANGELOG.md 中有多个目标分支没有的版本段落："
            + "、".join(v for v, _ in pending)
            + "；一批改动只对应一个版本号，请先手工合并成一段"
        )

    candidates = [v for v in (base_version, main_version) if v is not None]
    if not pending:
        candidates.append(tree_version)
    start = max(candidates, key=Version.key)
    target = Version.parse(args.explicit) if args.explicit else next_version(start, level)
    if target.key() <= start.key():
        raise SystemExit(f"新版本 {target} 必须大于当前最高版本 {start}")
    if str(target) in base_sections:
        raise SystemExit(f"{args.base_ref} 的 CHANGELOG 已有 {target} 段落")

    print(f"当前工作树：{tree_version}")
    print(f"{args.base_ref}：{base_version or '（读取失败）'}")
    print(f"{args.main_ref}：{main_version or '（读取失败）'}")
    if pending:
        print(f"本分支未合并的版本段落：{pending[0][0]}（将顺延为 {target}）")
    print(f"下一个版本：{start} → {target}")

    date = args.date or release_date()
    effective_level = infer_level(start, target) if args.explicit else level
    if not target.prerelease:
        label = args.label or stable_label_for(target)
    else:
        label = args.label or LEVEL_LABELS[effective_level]
    # 顺延已有段落时默认保留原级别标签；只有显式 --label 或 --level minor/major/stable 才改写。
    rename_label = args.label or (label if (not args.explicit and level != "patch") else None)

    if pending:
        _, pending_chunk = pending[0]
        moved = rewrite_header(pending_chunk, target, date, rename_label, args.title)
        if unreleased.strip():
            # rebase 期间又写进 Unreleased 的内容并入本段，避免遗漏。
            first, _, body = moved.partition("\n")
            moved = first + "\n\n" + unreleased.strip("\n") + "\n" + body
            unreleased = "\n"
        others = [chunk for v, chunk in sections if chunk is not pending_chunk]
        new_changelog = head + "\n" + moved.rstrip("\n") + "\n\n" + "".join(others)
    else:
        if not unreleased.strip():
            raise SystemExit("CHANGELOG.md 的 Unreleased 为空：先把本批改动写进 Unreleased（中文），再运行本脚本")
        if not args.title:
            raise SystemExit("缺少版本主题：请用 --title 指定")
        section = render_header(target, date, label, args.title) + unreleased.rstrip("\n") + "\n\n"
        new_changelog = head + "\n" + section + "".join(chunk for _, chunk in sections)

    if args.dry_run:
        print("（--dry-run：未写入文件）")
        return 0

    write_versions(target, dry_run=False)
    changelog_path.write_text(new_changelog, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-version-sync.py"), "--root", str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    print("已写入：" + "、".join(VERSION_FILES))
    print("提示：pyproject.toml 与 frontend/src/lib/version.ts 自动跟随，无需修改；iOS OpenAPI 副本不带服务器版本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
