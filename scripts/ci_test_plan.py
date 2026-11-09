#!/usr/bin/env python3
"""Select affected tests conservatively; unknown impact falls back to the full suite."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANNER_INPUTS = {
    "scripts/ci_test_plan.py",
    "scripts/ci_run_tests.py",
    "scripts/ci-test-map.json",
    "scripts/ci_change_scope.py",
    "scripts/ci_baseline.py",
    "scripts/ci_frontend_impact.cjs",
    "scripts/ci_frontend_impact.test.cjs",
    "ios/scripts/ui-test-shards.py",
}
BACKEND_SHARED = (
    "backend/app/models",
    "backend/app/db",
    "backend/app/config",
    "backend/app/deps.py",
    "backend/app/main.py",
    "backend/app/worker/",
    "backend/app/middleware",
    "backend/app/services/event_bus.py",
    "backend/app/services/plugin",
    "backend/app/services/auth",
    "backend/app/services/login",
    "backend/app/services/permissions",
    "backend/app/api/auth",
    "backend/app/security",
    "backend/app/crypto",
    "backend/alembic/",
)


@dataclass
class Selection:
    mode: str
    tests: list[str] | None
    reason: str


def full(reason: str) -> Selection:
    return Selection("full", None, reason)


def skip(reason: str) -> Selection:
    return Selection("skip", [], reason)


def changed_paths(root: Path, base: str, head: str) -> list[str]:
    if not base or set(base) == {"0"}:
        raise ValueError("没有可靠的比较基线")
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.split("\0") if p]


def snapshot(root: Path, prefix: str, revision: str = "") -> dict[str, str]:
    if not revision:
        return {
            p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in (root / prefix).rglob("*")
            if p.is_file() and p.suffix in {".py", ".swift"}
        }
    data = subprocess.check_output(
        ["git", "archive", revision, "--", prefix], cwd=root, stderr=subprocess.PIPE
    )
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {
            m.name: archive.extractfile(m).read().decode("utf-8")
            for m in archive.getmembers()
            if m.isfile() and Path(m.name).suffix in {".py", ".swift"}
        }


def suite_hash(root: Path, target: str) -> str:
    folder = root / ("frontend/tests" if target == "browser" else "ios/TelePilotUITests")
    digest = hashlib.sha256()
    for path in sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix in {".ts", ".tsx", ".js", ".json", ".swift"}
    ):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def version_only_paths(root: Path, paths: list[str], base: str, head: str) -> set[str]:
    """Ignore only proven metadata-only edits, never dependency or API changes."""
    ignored = set()
    candidates = {"backend/app/__init__.py", "frontend/package.json", "openapi/telepilot.openapi.json"}
    for path in candidates.intersection(paths):
        if not base:
            continue
        try:
            before, after = [
                subprocess.check_output(
                    ["git", "show", f"{rev}:{path}"], cwd=root, stderr=subprocess.PIPE
                ).decode()
                for rev in (base, head)
            ]
            if path.endswith(".py"):
                pattern = r'^__version__ = "[^"\n]+"[ \t]*$'
                if all(
                    len(re.findall(pattern, value, re.MULTILINE)) == 1 for value in (before, after)
                ) and re.sub(pattern, "VERSION", before, flags=re.MULTILINE) == re.sub(
                    pattern, "VERSION", after, flags=re.MULTILINE
                ):
                    ignored.add(path)
            else:
                left, right = json.loads(before), json.loads(after)
                if path.startswith("openapi/"):
                    del left["info"]["version"]
                    del right["info"]["version"]
                else:
                    del left["version"]
                    del right["version"]
                if left == right:
                    ignored.add(path)
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError):
            continue
    return ignored


def ios_ids(root: Path) -> set[str]:
    result = set()
    for path in (root / "ios/TelePilotUITests").glob("*.swift"):
        current = None
        classes = set()
        for line in path.read_text().splitlines():
            match = re.match(r"\s*(?:final\s+)?class\s+(\w+)\s*:\s*XCTestCase\b", line)
            extension = re.match(r"\s*extension\s+(\w+)\b", line)
            method = re.match(r"\s*(?:@MainActor\s+)?func\s+(test\w*)\s*\(\s*\)", line)
            if match:
                current = match[1]
                classes.add(current)
            elif extension:
                current = extension[1] if extension[1] in classes else None
            elif re.match(r"^(?:private\s+|fileprivate\s+|final\s+)*(?:class|struct|enum|actor)\s+", line):
                current = None
            elif current and method:
                result.add(current + "/" + method[1])
    return result


def swift_leaf_declarations(text: str) -> bool:
    # Unknown Swift lexical forms cannot justify reducing UI coverage.
    if '"""' in text or re.search(r'#+"', text):
        return False
    cleaned = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"//[^\n]*", "", cleaned)
    if "/*" in cleaned or "*/" in cleaned:
        return False
    depth = 0
    for token in re.findall(r"[{}]|\b(?:extension|func|let|var|typealias)\b", cleaned):
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0:
            return False
    return depth == 0


def ios_dependency_guard(root: Path, paths: list[str], mapping: dict, base: str) -> bool:
    try:
        versions = [snapshot(root, "ios/TelePilotApp")]
        if base:
            versions.append(snapshot(root, "ios/TelePilotApp", base))
        for files in versions:
            for path in paths:
                if path not in files:
                    return False
                text = files[path]
                if not swift_leaf_declarations(text):
                    return False
                names = re.findall(
                    r"^\s*(?:(?:private|fileprivate|final|public|internal)\s+)*(?:struct|class|enum|actor|protocol|typealias)\s+(\w+)",
                    text,
                    re.MULTILINE,
                )
                if not names:
                    return False
                pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b")
                consumers = {p for p, content in files.items() if p != path and pattern.search(content)}
                if not consumers <= set(mapping.get("ios_consumers", {}).get(path, [])):
                    return False
                for consumer in consumers:
                    references = set(pattern.findall(files[consumer]))
                    allowed = (
                        mapping.get("ios_consumer_symbols", {}).get(path, {}).get(consumer, [Path(path).stem])
                    )
                    if not references <= set(allowed):
                        return False
        return True
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError):
        return False


def _docs(path: str) -> bool:
    return path.startswith("docs/") or Path(path).suffix.lower() in {".md", ".rst"}


def python_graph(root: Path, texts: dict[str, str] | None = None):
    modules = {}
    trees = {}
    for relative, content in (texts if texts is not None else snapshot(root, "backend/app")).items():
        path = Path(relative)
        parts = path.relative_to("backend").with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        modules[module] = relative
        trees[relative] = ast.parse(content, filename=relative)
    reverse: dict[str, set[str]] = {p: set() for p in trees}
    opaque_tests = set()
    for module, path in modules.items():
        package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
        imports = set()
        tree = trees[path]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    prefix = package.split(".")[: len(package.split(".")) - node.level + 1]
                    base = ".".join(prefix + ([base] if base else []))
                imports.add(base)
                imports.update(base + "." + a.name for a in node.names)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("app.")
            ):
                # Covers literal monkeypatch/importlib targets in integration tests.
                imports.add(node.value)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and path.startswith("backend/app/tests/")
                and node.func.attr in {"read_text", "getsource", "spec_from_file_location"}
            ):
                opaque_tests.add(path)
        for name in imports:
            while name and name not in modules:
                name = name.rpartition(".")[0]
            if name in modules:
                reverse[modules[name]].add(path)
    return reverse, opaque_tests


def fixture_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for p in sorted((root / "backend").rglob("conftest.py")):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        digest.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return digest.hexdigest()


def select_backend(root: Path, paths: list[str], mapping: dict, base: str = "") -> Selection:
    relevant = [p for p in paths if p.startswith("backend/") and not _docs(p)]
    if not relevant:
        if any(p.startswith(("scripts/", "deploy/", "plugins/")) for p in paths if not _docs(p)):
            return full("运行脚本或插件边界变化，保留后端完整检查")
        return skip("没有后端改动")
    if mapping.get("backend_fixture_hash") != fixture_hash(root):
        return full("全局 fixture 已变化，依赖边界需要复核")
    if any(p in mapping.get("backend_fixture_modules", []) for p in relevant):
        return full("直接影响全局 fixture 的模块变化")
    if any(
        p.startswith(BACKEND_SHARED) or p.endswith(("conftest.py", "__init__.py")) or not p.endswith(".py")
        for p in relevant
    ):
        return full("后端公共基础能力、依赖或数据结构变化")
    if any(not (root / p).is_file() for p in relevant):
        return full("有删除或重命名，不能仅根据当前依赖图缩小测试")
    try:
        reverse, opaque = python_graph(root)
        if base:
            previous, previous_opaque = python_graph(root, snapshot(root, "backend/app", base))
            opaque.update(previous_opaque)
            for path, parents in previous.items():
                reverse.setdefault(path, set()).update(parents)
    except (SyntaxError, OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError):
        return full("后端依赖图无法可靠解析")
    affected = set(relevant)
    pending = list(relevant)
    while pending:
        for parent in reverse.get(pending.pop(), set()) - affected:
            affected.add(parent)
            pending.append(parent)
    # 每个选中的用例仍执行全局 fixture；仅导入服务的传递边不等于所有用例
    # 都调用该服务。fixture 本身和其直接执行/重置的模块已在上面强制全量。
    tests = {p for p in affected if p.startswith("backend/app/tests/test_") and p.endswith(".py")}
    if not tests or any(p not in reverse for p in relevant):
        return full("改动未找到可靠的测试依赖")
    selected = tests | {p for p in opaque if Path(p).name.startswith("test_")} | set(mapping["backend_smoke"])
    if any(not (root / p).is_file() for p in selected):
        return full("基础用例或测试映射缺失")
    return Selection("selected", sorted(selected), "Python 导入及反向依赖 + 基础鉴权/接口检查 + 源码守护测试")


def select_mapped(root: Path, paths: list[str], mapping: dict, target: str, base: str = "") -> Selection:
    prefix = "frontend/" if target == "browser" else "ios/"
    relevant = [p for p in paths if p.startswith(prefix) and not _docs(p)]
    if target == "ios" and any(p.startswith(("openapi/", "schemas/")) or "modelBrand" in p for p in paths):
        return full("跨端接口或共享资源变化")
    if not relevant:
        return skip("没有相关源码改动")
    if mapping.get(target + "_suite_hash") != suite_hash(root, target):
        return full("测试集已变化，页面覆盖映射需要复核")
    selected = set(mapping[target + "_smoke"])
    for path in relevant:
        if not (root / path).is_file():
            return full("删除或重命名需要完整回归")
        if (
            target == "browser"
            and path.startswith(("frontend/tests/a11y/", "frontend/tests/visual/"))
            and path.endswith(".spec.ts")
        ):
            selected.add(path)
        elif path in mapping[target]:
            selected.update(mapping[target][path])
        else:
            return full("共享代码、配置或尚未建立覆盖映射：" + path)
    if not selected or not mapping[target + "_smoke"]:
        return full("基础冒烟用例未配置")
    if target == "browser" and any(not (root / p).is_file() for p in selected):
        return full("浏览器覆盖映射指向不存在的用例")
    if target == "ios" and (
        not selected <= ios_ids(root) or not ios_dependency_guard(root, relevant, mapping, base)
    ):
        return full("iOS 测试标识或页面依赖边界无法确认")
    return Selection("selected", sorted(selected), "已核对的模块覆盖映射 + 固定基础冒烟检查")


def requires_full(paths: list[str]) -> bool:
    docs = {".env.example", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "README.md"}
    prefixes = (
        "backend/",
        "frontend/",
        "ios/",
        "gateway/",
        "deploy/",
        "plugins/",
        "scripts/",
        "schemas/",
        "openapi/",
        "examples/",
    )
    return not paths or any(
        p in PLANNER_INPUTS
        or p.startswith(".github/workflows/")
        or (p not in docs and not _docs(p) and not p.startswith(prefixes))
        for p in paths
    )


def build_plan(
    root: Path,
    paths: list[str],
    force_full: bool = False,
    *,
    base: str = "",
    ignored: set[str] | None = None,
    target: str = "all",
) -> dict:
    try:
        mapping = json.loads((root / "scripts/ci-test-map.json").read_text())
        required = {"backend_smoke", "browser_smoke", "ios_smoke", "browser", "ios"}
        if not required <= set(mapping):
            raise ValueError("映射结构不完整")
        force_full = force_full or requires_full(paths)
        effective = [p for p in paths if p not in (ignored or set())]
        selections = (
            {target: full("正式发布、手动全量或测试选择规则变化") for target in ("backend", "browser", "ios")}
            if force_full
            else {
                "backend": select_backend(root, effective, mapping, base)
                if target in {"all", "ci"}
                else skip("本工作流不运行后端测试"),
                "browser": select_mapped(root, effective, mapping, "browser", base)
                if target in {"all", "ci"}
                else skip("本工作流不运行浏览器测试"),
                "ios": select_mapped(root, effective, mapping, "ios", base)
                if target in {"all", "ios"}
                else skip("iOS 由独立工作流校验"),
            }
        )
    except (OSError, ValueError, KeyError, TypeError):
        force_full = True
        effective = paths
        selections = {
            target: full("测试映射读取失败，保守执行全量") for target in ("backend", "browser", "ios")
        }
    result = {target: asdict(selection) for target, selection in selections.items()}
    ios = result["ios"]
    count = 4 if ios["mode"] == "full" else max(1, min(4, (len(ios["tests"]) + 11) // 12))
    ios["shards"] = list(range(1, count + 1))
    result["context"] = {"base": base, "paths": effective, "force_full": force_full}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--target", choices=["all", "ci", "ios"], default="all")
    parser.add_argument("--paths-file", type=Path, help="JSON array of changed repository paths")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-summary", type=Path)
    args = parser.parse_args()
    try:
        paths = (
            json.loads(args.paths_file.read_text())
            if args.paths_file
            else changed_paths(args.root, args.base, args.head)
        )
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise ValueError("文件清单格式错误")
        ignored = version_only_paths(args.root, paths, args.base, args.head)
        plan = build_plan(
            args.root, paths, args.force_full, base=args.base, ignored=ignored, target=args.target
        )
    except (OSError, ValueError, subprocess.CalledProcessError):
        plan = build_plan(args.root, [], True)
    encoded = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    print(encoded)
    if args.github_summary:
        with args.github_summary.open("a") as handle:
            handle.write("### 本次测试范围\n\n")
            for target in ("backend", "browser", "ios"):
                item = plan[target]
                count = "全部" if item["tests"] is None else str(len(item["tests"]))
                handle.write(f"- {target}: {item['mode']}，{count} 个测试文件/方法；{item['reason']}\n")
            handle.write("\n浏览器安装依赖后会再用 TypeScript 解析器核对 base/head 的引用关系。\n")
    if args.output:
        args.output.write_text(encoded + "\n")
    if args.github_output:
        with args.github_output.open("a") as handle:
            handle.write("plan=" + encoded + "\n")
            handle.write("ios_selection=" + json.dumps(plan["ios"]["tests"]) + "\n")
            handle.write("ios_shards=" + json.dumps(plan["ios"]["shards"]) + "\n")
            handle.write("ios_total=" + str(len(plan["ios"]["shards"])) + "\n")
            handle.write("ios_skip=" + ("true" if plan["ios"]["mode"] == "skip" else "false") + "\n")


if __name__ == "__main__":
    main()
