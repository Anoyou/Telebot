#!/usr/bin/env python3
"""校验 iOS App 自身版本设置：project.yml 与 TelePilot.xcodeproj 必须一致且符合 Apple 规范。

iOS App 使用独立于 TelePilot 服务器的版本号（见 AGENTS.md「iOS 版本」）：
- MARKETING_VERSION（CFBundleShortVersionString）必须是纯数字 X.Y.Z，不能带 -beta 等后缀；
- CURRENT_PROJECT_VERSION（CFBundleVersion）在工程里只是本地默认值，CI 打包时注入
  单调递增的 build 号，这里只要求它是正整数且两处一致；
- ios/CHANGELOG.md 最新的正式版本段落必须就是当前 MARKETING_VERSION（App 内“更新日志”读取该文件）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

IOS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_YML = IOS_ROOT / "project.yml"
PBXPROJ = IOS_ROOT / "TelePilot.xcodeproj" / "project.pbxproj"
CHANGELOG = IOS_ROOT / "CHANGELOG.md"
KEYS = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
PATTERNS = {
    "MARKETING_VERSION": re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"),
    "CURRENT_PROJECT_VERSION": re.compile(r"^[1-9]\d*$"),
}


def yml_values(text: str) -> dict[str, list[str]]:
    return {key: re.findall(rf"^\s*{key}:\s*\"?([^\"\s#]+)\"?", text, re.M) for key in KEYS}


def pbx_values(text: str) -> dict[str, list[str]]:
    return {key: re.findall(rf"^\s*{key} = \"?([^\";]+)\"?;", text, re.M) for key in KEYS}


def main() -> int:
    yml = yml_values(PROJECT_YML.read_text(encoding="utf-8"))
    pbx = pbx_values(PBXPROJ.read_text(encoding="utf-8"))
    problems: list[str] = []
    resolved: dict[str, str] = {}
    for key in KEYS:
        if len(yml[key]) != 1:
            problems.append(f"project.yml 中 {key} 应恰好出现一次，实际 {len(yml[key])} 次")
            continue
        value = yml[key][0]
        resolved[key] = value
        if not PATTERNS[key].match(value):
            hint = "纯数字 X.Y.Z（不能带 -beta 等后缀）" if key == "MARKETING_VERSION" else "正整数"
            problems.append(f"project.yml 中 {key}={value} 不合规，应为{hint}")
        if not pbx[key]:
            problems.append(f"project.pbxproj 中缺少 {key}")
        for other in sorted(set(pbx[key]) - {value}):
            problems.append(f"project.pbxproj 中 {key}={other} 与 project.yml 的 {value} 不一致")

    marketing = resolved.get("MARKETING_VERSION")
    if marketing:
        if not CHANGELOG.exists():
            problems.append("缺少 ios/CHANGELOG.md")
        else:
            released = [
                v for v in re.findall(r"^## \[([^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"), re.M)
                if v.lower() != "unreleased"
            ]
            if not released or released[0] != marketing:
                latest = released[0] if released else "无"
                problems.append(
                    f"ios/CHANGELOG.md 最新版本段落为 {latest}，应为当前 MARKETING_VERSION {marketing}"
                    "（bump 版本时把 Unreleased 移到新版本段落）"
                )

    if problems:
        print("iOS 版本设置有问题（project.yml 与 TelePilot.xcodeproj 需同步修改）：", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(
        f"iOS 版本一致：MARKETING_VERSION={resolved['MARKETING_VERSION']}，"
        f"本地默认 CURRENT_PROJECT_VERSION={resolved['CURRENT_PROJECT_VERSION']}（CI 打包时注入实际 build 号）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
