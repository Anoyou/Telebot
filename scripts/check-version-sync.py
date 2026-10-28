#!/usr/bin/env python3
"""校验 TelePilot 服务器版本号在所有版本源之间一致（CI「版本同步」与 bump-version.py 共用）。

版本源（第十一轮起从五处减为三处字面量）：
- ``backend/app/__init__.py`` 的 ``__version__``：后端单点；``backend/pyproject.toml`` 用
  ``dynamic = ["version"]`` + ``[tool.setuptools.dynamic] version = {attr = "app.__version__"}`` 读取它。
- ``frontend/package.json`` 的 ``version``：前端单点；``frontend/src/lib/version.ts`` 从它导入。
- ``openapi/telepilot.openapi.json`` 的 ``info.version``：发布快照（``make codegen`` 或 bump 脚本写入）。

另外校验：
- ``CHANGELOG.md`` 有当前精确版本段落 ``## [版本]``；
- iOS 的 OpenAPI 原生子集不携带服务器版本（``info.version`` 固定为 ``native-subset``），
  否则服务器升版本会改动 ``ios/**`` 并触发 iOS 全量测试。

仅依赖标准库。用法：``python3 scripts/check-version-sync.py [--root .] [--quiet]``。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 宿主 Python 过旧时退化为文本检查
    tomllib = None  # type: ignore[assignment]

NATIVE_INFO_VERSION = "native-subset"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?$")
INIT_RE = re.compile(r'^__version__ = "([^"]+)"', re.MULTILINE)


def read_versions(root: Path) -> dict[str, str]:
    init_text = (root / "backend/app/__init__.py").read_text(encoding="utf-8")
    match = INIT_RE.search(init_text)
    if not match:
        raise SystemExit("backend/app/__init__.py 缺少 __version__ = \"...\"")
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    openapi = json.loads((root / "openapi/telepilot.openapi.json").read_text(encoding="utf-8"))
    return {
        "backend/app/__init__.py": match.group(1),
        "frontend/package.json": str(package.get("version")),
        "openapi/telepilot.openapi.json": str(openapi["info"]["version"]),
    }


def structural_problems(root: Path) -> list[str]:
    problems: list[str] = []
    pyproject_text = (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        data = tomllib.loads(pyproject_text)
        project = data.get("project") or {}
        if "version" in project:
            problems.append("backend/pyproject.toml 不应再写静态 version（改由 app.__version__ 动态读取）")
        if "version" not in (project.get("dynamic") or []):
            problems.append('backend/pyproject.toml 的 [project] 需要 dynamic = ["version"]')
        attr = (((data.get("tool") or {}).get("setuptools") or {}).get("dynamic") or {}).get("version") or {}
        if attr.get("attr") != "app.__version__":
            problems.append('backend/pyproject.toml 需要 [tool.setuptools.dynamic] version = {attr = "app.__version__"}')
    elif re.search(r'^version = "', pyproject_text, re.MULTILINE):
        problems.append("backend/pyproject.toml 不应再写静态 version（改由 app.__version__ 动态读取）")

    version_ts = (root / "frontend/src/lib/version.ts").read_text(encoding="utf-8")
    if "../../package.json" not in version_ts or re.search(r'APP_VERSION(?:: string)? = "', version_ts):
        problems.append("frontend/src/lib/version.ts 应从 ../../package.json 读取版本，不能写字面量")

    native = root / "ios/TelePilotCore/Sources/TelePilotCore/openapi.json"
    if native.exists():
        native_version = json.loads(native.read_text(encoding="utf-8")).get("info", {}).get("version")
        if native_version != NATIVE_INFO_VERSION:
            problems.append(
                f"iOS OpenAPI 原生子集的 info.version 应固定为 {NATIVE_INFO_VERSION}，当前为 {native_version!r}"
                "（运行 python3 ios/TelePilotCore/scripts/sync-openapi.py）"
            )
    return problems


def check(root: Path) -> str:
    versions = read_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        raise SystemExit(f"版本号不一致: {versions}")
    version = unique.pop()
    if not VERSION_RE.match(version):
        raise SystemExit(f"版本格式不符合发布约定：{version}")
    problems = structural_problems(root)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        problems.append(f"CHANGELOG.md 缺少当前精确版本段：## [{version}]")
    if problems:
        raise SystemExit("版本同步检查未通过：\n  - " + "\n  - ".join(problems))
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    version = check(args.root.resolve())
    if not args.quiet:
        print(f"version sync ok: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
