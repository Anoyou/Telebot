#!/usr/bin/env python3
"""同步插件声明式 UI 契约（schemas/plugin-ui-widgets.json）到后端、Web 与 iOS。

规范来源：schemas/plugin-ui-widgets.json（控件、x-ui-* 键、卡片 DSL、钩子超时）。
副本（逐字节复制，不要手改）：
- backend/app/worker/plugins/plugin_ui_widgets.json        宿主校验 manifest、GET /api/plugins/ui-contract
- frontend/src/lib/pluginUiWidgets.json                    Web ConfigDialog / PluginCards 与单测
- ios/TelePilotCore/Sources/TelePilotCore/plugin-ui-widgets.json  iOS 控件解析、卡片与单测

`--check` 只校验不写入（CI「后端」与「iOS」工作流运行）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "schemas/plugin-ui-widgets.json"
COPIES = (
    REPO / "backend/app/worker/plugins/plugin_ui_widgets.json",
    REPO / "frontend/src/lib/pluginUiWidgets.json",
    REPO / "ios/TelePilotCore/Sources/TelePilotCore/plugin-ui-widgets.json",
)


def validate(contract: dict) -> list[str]:
    problems: list[str] = []
    widgets = contract.get("widgets") or []
    ids = [item.get("id") for item in widgets]
    if len(ids) != len(set(ids)):
        problems.append("widgets[].id 有重复")
    if contract.get("resolution_order") != ids:
        problems.append("resolution_order 必须与 widgets 顺序一致")
    for item in widgets:
        example = item.get("example") or {}
        if not example.get("key") or not isinstance(example.get("schema"), dict):
            problems.append(f"控件 {item.get('id')} 缺少 example.key / example.schema")
        explicit = item.get("x_ui_widget")
        if explicit and example.get("schema", {}).get("x-ui-widget") != explicit:
            problems.append(f"控件 {item.get('id')} 的示例没有写 x-ui-widget: {explicit}")
    layouts = [item.get("type") for item in (contract.get("cards") or {}).get("layouts") or []]
    if sorted(layouts) != sorted(["stats", "table", "kv", "list", "buttons"]):
        problems.append(f"首批卡片布局应为 stats/table/kv/list/buttons，实际 {layouts}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()
    text = SOURCE.read_text(encoding="utf-8")
    problems = validate(json.loads(text))
    stale = [path for path in COPIES if not path.exists() or path.read_text(encoding="utf-8") != text]
    if args.check:
        problems.extend(f"副本与规范来源不一致：{path.relative_to(REPO)}（运行 python3 scripts/sync-plugin-ui-widgets.py）" for path in stale)
    else:
        for path in stale:
            path.write_text(text, encoding="utf-8")
            print(f"已更新 {path.relative_to(REPO)}")
    if problems:
        for line in problems:
            print(f"✗ {line}", file=sys.stderr)
        return 1
    print(f"插件 UI 契约一致：{len(json.loads(text)['widgets'])} 个控件，{len(COPIES)} 份副本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
