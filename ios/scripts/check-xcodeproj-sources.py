#!/usr/bin/env python3
"""检查手工维护的 TelePilot.xcodeproj 是否收录了 App 与 UI 测试目录下的全部 Swift 源文件。

project.yml 是 XcodeGen 定义，但仓库同时提交生成后的 .xcodeproj；手工新增文件时
容易漏掉 PBXFileReference / PBXBuildFile。这里只做源文件清单比对，不要求安装 XcodeGen。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

IOS_ROOT = Path(__file__).resolve().parents[1]
PBXPROJ = IOS_ROOT / "TelePilot.xcodeproj" / "project.pbxproj"
SOURCE_DIRS = ("TelePilotApp", "TelePilotUITests")


def main() -> int:
    text = PBXPROJ.read_text(encoding="utf-8")
    file_refs = set(re.findall(r"/\* ([^*/]+\.swift) \*/ = \{isa = PBXFileReference;", text))
    build_files = set(re.findall(r"/\* ([^*/]+\.swift) in Sources \*/ = \{isa = PBXBuildFile;", text))
    on_disk = {p.name: p for d in SOURCE_DIRS for p in (IOS_ROOT / d).rglob("*.swift")}

    problems: list[str] = []
    for name, path in sorted(on_disk.items()):
        rel = path.relative_to(IOS_ROOT)
        if name not in file_refs:
            problems.append(f"缺少文件引用：{rel}")
        if name not in build_files:
            problems.append(f"未加入编译阶段：{rel}")
    for name in sorted(file_refs - set(on_disk)):
        problems.append(f"工程引用了不存在的文件：{name}")

    if problems:
        print("TelePilot.xcodeproj 与源文件不同步（修改后执行 `cd ios && xcodegen generate`）：", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"TelePilot.xcodeproj 已收录全部 {len(on_disk)} 个 Swift 源文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
