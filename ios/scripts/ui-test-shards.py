#!/usr/bin/env python3
"""把 TelePilotUITests 的全部用例按历史耗时均衡分到 N 个分片，供「iOS」工作流并行执行。

用例清单直接从 TelePilotUITests/*.swift 解析（`final class X: XCTestCase` 内的 `func testXxx()`），
新增用例无需手工登记，自动进入某个分片；ui-test-durations.json 只用于均衡耗时，
未登记的用例按默认耗时估算。所有分片的并集严格等于全部用例，不会漏测。

第十一轮：--durations-override 指向最近几次全绿 run 实测耗时的合并表（ui-test-durations-tool.py 生成，
「判定」job 统一下发，所有分片用同一份），其中有的用例优先用实测值，没有的再用仓库里的表。

用法：
  ui-test-shards.py --total 4 --shard 1     # 输出该分片的 -only-testing 参数（每行一个）
  ui-test-shards.py --total 4 --summary     # 打印每个分片的用例数与估算耗时
  ui-test-shards.py --total 4 --shard 1 --durations-override measured.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

IOS_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = IOS_ROOT / "TelePilotUITests"
TARGET = "TelePilotUITests"
DURATIONS = Path(__file__).with_name("ui-test-durations.json")
DEFAULT_SECONDS = 40

CLASS_RE = re.compile(r"^\s*(?:final\s+)?class\s+(\w+)\s*:\s*XCTestCase\b")
EXT_RE = re.compile(r"^\s*extension\s+(\w+)\b")
OTHER_TYPE_RE = re.compile(r"^(?:private\s+|fileprivate\s+|final\s+)*(?:class|struct|enum|actor)\s+(\w+)")
TEST_RE = re.compile(r"^\s*(?:@MainActor\s+)?func\s+(test\w*)\s*\(\s*\)")


def discover() -> list[str]:
    tests: list[str] = []
    for path in sorted(TEST_DIR.glob("*.swift")):
        current: str | None = None
        test_classes: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if m := CLASS_RE.match(line):
                current = m.group(1)
                test_classes.add(current)
            elif m := EXT_RE.match(line):
                current = m.group(1) if m.group(1) in test_classes else None
            elif (m := OTHER_TYPE_RE.match(line)) and not line.startswith((" ", "\t")):
                current = None
            elif current and (m := TEST_RE.match(line)):
                tests.append(f"{current}/{m.group(1)}")
    if not tests:
        raise SystemExit(f"没有在 {TEST_DIR} 找到任何 UI 测试用例")
    if len(tests) != len(set(tests)):
        raise SystemExit("解析到重复的 UI 测试用例名")
    return tests


def load_override(path: Path | None) -> dict[str, float]:
    if path is None or not path.is_file() or path.stat().st_size == 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"耗时覆盖表 {path} 无法解析，忽略", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, (int, float)) and v > 0}


def plan(total: int, override: dict[str, float] | None = None) -> list[list[tuple[str, float]]]:
    durations = json.loads(DURATIONS.read_text(encoding="utf-8")) if DURATIONS.exists() else {}
    durations.update(override or {})
    tests = discover()
    weighted = sorted(((t, float(durations.get(t, DEFAULT_SECONDS))) for t in tests), key=lambda x: (-x[1], x[0]))
    shards: list[list[tuple[str, float]]] = [[] for _ in range(total)]
    loads = [0.0] * total
    for test, seconds in weighted:  # 最长处理时间优先（LPT）贪心
        i = min(range(total), key=lambda k: (loads[k], k))
        shards[i].append((test, seconds))
        loads[i] += seconds
    assert sorted(t for s in shards for t, _ in s) == sorted(tests)
    return shards


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--shard", type=int, help="从 1 开始的分片序号")
    group.add_argument("--summary", action="store_true")
    parser.add_argument("--durations-override", type=Path, help="实测耗时覆盖表（JSON：类/用例 → 秒）")
    args = parser.parse_args()
    if args.total < 1:
        raise SystemExit("--total 必须 ≥ 1")
    override = load_override(args.durations_override)
    shards = plan(args.total, override)
    if args.summary:
        tests = {t for s in shards for t, _ in s}
        used = len(tests & override.keys())
        print(f"耗时来源：{'实测覆盖 ' + str(used) + ' 个用例，其余用 ui-test-durations.json' if used else 'ui-test-durations.json'}")
        for i, shard in enumerate(shards, 1):
            print(f"分片 {i}：{len(shard)} 个用例，估算 {sum(s for _, s in shard):.0f} 秒")
        print(f"共 {sum(len(s) for s in shards)} 个用例")
        return 0
    if not 1 <= args.shard <= args.total:
        raise SystemExit("--shard 超出范围")
    shard = shards[args.shard - 1]
    if not shard:
        raise SystemExit(f"分片 {args.shard} 没有用例（用例数少于分片数）")
    for test, _ in sorted(shard):
        print(f"-only-testing:{TARGET}/{test}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
