#!/usr/bin/env python3
"""导出插件稳定契约快照（schemas/plugin-api-contract.json）与详细 plugin.json Schema。

用法::

    python scripts/export-plugin-contract.py            # 重新生成
    python scripts/export-plugin-contract.py --check    # CI：快照过期或相对冻结基线有破坏性改动时失败
    python scripts/export-plugin-contract.py --freeze   # 仅在发布新的契约 MAJOR 时使用：写冻结基线
    python scripts/export-plugin-contract.py --freeze-scope   # 写 1.0 冻结范围（第 22 轮已写入；以后实验项转正时追加带日期的新文件）
    python scripts/export-plugin-contract.py --validate-plugins ../telebot-plugins   # 用详细 Schema 校验插件库全部 plugin.json

冻结基线 schemas/plugin-api-contract-1.0.frozen.json 与 1.0 冻结范围 schemas/plugin-api-contract-1.0-scope.frozen.json
一旦提交就不再修改（见 docs/PLUGIN-CONTRACT.md）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

os.environ.setdefault("MASTER_KEY", "4QAy7kFzv2mkHRXTHwL_FEZABz0R3sqat5rBl4vDLXk=")
os.environ.setdefault("JWT_SECRET", "telepilot-plugin-contract-export-only")
sys.path.insert(0, str(BACKEND))

from app.worker.plugins import contract


def _schema_text() -> str:
    return json.dumps(contract.build_manifest_schema(), ensure_ascii=False, indent=2) + "\n"


def _validate_plugins(root: Path) -> int:
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(contract.build_manifest_schema())
    files = sorted(root.glob("*/plugin.json"))
    errors = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"::error file={path}::plugin.json 不是合法 JSON：{exc}", file=sys.stderr)
            errors += 1
            continue
        for err in validator.iter_errors(data):
            where = "/".join(str(part) for part in err.absolute_path) or "(顶层)"
            print(f"::error file={path}::{where}: {err.message}", file=sys.stderr)
            errors += 1
    print(f"按插件契约 {contract.PLUGIN_CONTRACT_VERSION} 校验 {len(files)} 个 plugin.json，错误 {errors} 处")
    return 1 if errors or not files else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只检查，不写文件")
    parser.add_argument("--freeze", action="store_true", help="把当前快照写成冻结基线（仅新契约 MAJOR）")
    parser.add_argument(
        "--freeze-scope",
        action="store_true",
        help="把当前 stability 段的稳定部分写成 1.0 冻结范围文件；主文件已存在时追加带日期的新文件，从不覆盖",
    )
    parser.add_argument("--validate-plugins", type=Path, help="用 plugin-contract.schema.json 校验该目录下每个 */plugin.json")
    args = parser.parse_args()

    if args.validate_plugins:
        return _validate_plugins(args.validate_plugins)

    current = contract.build_contract()
    snapshot_text = contract.dumps(current)
    schema_text = _schema_text()
    targets = {
        contract.CONTRACT_PATH: snapshot_text,
        contract.CONTRACT_SCHEMA_PATH: schema_text,
    }

    if args.check:
        failed = False
        for path, text in targets.items():
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"::error::{path.relative_to(ROOT)} 已过期：运行 python scripts/export-plugin-contract.py 并提交", file=sys.stderr)
                failed = True
        problems = list(contract.iter_contract_problems(current))
        for item in problems:
            print(f"::error::插件契约破坏性改动：{item}", file=sys.stderr)
        if problems:
            print(
                f"相对冻结基线 {contract.FROZEN_BASELINE_PATH.relative_to(ROOT)} 有 {len(problems)} 处破坏性改动；"
                "契约 1.x 期间只允许新增（见 docs/PLUGIN-CONTRACT.md「变更规则」）；"
                f"$.stability 开头的条目对照 {contract.SCOPE_BASELINE_PATH.relative_to(ROOT)}（1.0 冻结范围）。",
                file=sys.stderr,
            )
        if failed or problems:
            return 1
        print(f"插件契约 {contract.PLUGIN_CONTRACT_VERSION} 快照最新，相对冻结基线无破坏性改动")
        return 0

    for path, text in targets.items():
        path.write_text(text, encoding="utf-8")
        print(path.relative_to(ROOT))
    if args.freeze:
        contract.FROZEN_BASELINE_PATH.write_text(snapshot_text, encoding="utf-8")
        print(contract.FROZEN_BASELINE_PATH.relative_to(ROOT))
    if args.freeze_scope:
        target = contract.SCOPE_BASELINE_PATH
        if target.exists():
            import datetime

            stamp = datetime.date.today().strftime("%Y%m%d")
            target = target.with_name(f"plugin-api-contract-1.0-scope-{stamp}.frozen.json")
            if target.exists():
                print(f"::error::{target.relative_to(ROOT)} 已存在，冻结范围文件不允许覆盖", file=sys.stderr)
                return 1
        target.write_text(contract.dumps(contract.scope_of(current)), encoding="utf-8")
        print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
