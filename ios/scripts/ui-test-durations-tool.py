#!/usr/bin/env python3
"""UI 测试耗时表自动更新（第十一轮）：从 xcresult 导出逐用例耗时，并合并最近几次全绿 run 的测量值。

「iOS」工作流里：
- 每个 UI 分片测试结束后 ``xcrun xcresulttool get test-results tests --path X.xcresult`` 导出 JSON，
  用 ``extract`` 转成 ``{"类/用例": 秒}``，作为本 run 的分片耗时工件；
- 全绿时汇总 job 用 ``merge`` 合并 4 个分片的耗时，上传 ``ios-ui-durations``（保留 30 天）；
- 下一个 run 的「判定」job 下载最近几份 ``ios-ui-durations``，再用 ``merge``（逐用例取中位数）得到
  覆盖表，以 job 输出传给编译 job 与所有分片，``ui-test-shards.py --durations-override`` 优先使用它。
  所有分片用同一份覆盖表计算，分片划分保持一致；取不到时回退到仓库里的 ui-test-durations.json。

用法：
  ui-test-durations-tool.py extract --input tests.json --output durations.json
  ui-test-durations-tool.py merge --output merged.json a.json b.json ...   # 逐用例中位数；单个文件即原样合并
仅依赖标准库。任何解析问题都只打印警告并以 0 退出（不影响测试结论）。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(h|m|s|ms)\b")
MAX_SECONDS = 1800  # 单个用例超过 30 分钟视为异常值丢弃


def parse_duration(node: dict) -> float | None:
    value = node.get("durationInSeconds")
    if isinstance(value, (int, float)):
        return float(value)
    text = node.get("duration")
    if not isinstance(text, str):
        return None
    total = 0.0
    found = False
    for number, unit in DURATION_PART_RE.findall(text):
        found = True
        total += float(number) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total if found else None


def test_id(node: dict, suite: str | None) -> str | None:
    identifier = node.get("nodeIdentifier")
    if isinstance(identifier, str) and "/" in identifier:
        parts = identifier.strip("/").split("/")
        name = f"{parts[-2]}/{parts[-1]}"
    else:
        name_part = node.get("name")
        if not isinstance(name_part, str) or not suite:
            return None
        name = f"{suite}/{name_part}"
    name = name.removesuffix("()")
    return name if re.fullmatch(r"\w+/test\w*", name) else None


def collect(node: object, suite: str | None, out: dict[str, float]) -> None:
    if isinstance(node, list):
        for item in node:
            collect(item, suite, out)
        return
    if not isinstance(node, dict):
        return
    node_type = node.get("nodeType")
    if node_type == "Test Suite" and isinstance(node.get("name"), str):
        suite = node["name"]
    if node_type == "Test Case":
        name = test_id(node, suite)
        seconds = parse_duration(node)
        if seconds is None:
            # 没有总耗时时累加各次重复（Repetition）的耗时。
            reps = [parse_duration(child) for child in node.get("children") or [] if isinstance(child, dict)]
            reps = [r for r in reps if r is not None]
            seconds = sum(reps) if reps else None
        if name and seconds is not None and 0 < seconds < MAX_SECONDS:
            out[name] = out.get(name, 0.0) + seconds
        return
    for key in ("testNodes", "children"):
        if key in node:
            collect(node[key], suite, out)


def extract(args: argparse.Namespace) -> int:
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"::warning::无法读取 xcresult 测试结果：{error}")
        return 0
    durations: dict[str, float] = {}
    collect(data, None, durations)
    if not durations:
        print("::warning::xcresult 中没有解析到用例耗时，跳过导出")
        return 0
    rounded = {name: max(1, round(seconds)) for name, seconds in sorted(durations.items())}
    Path(args.output).write_text(json.dumps(rounded, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"导出 {len(rounded)} 个用例耗时，合计 {sum(rounded.values())} 秒")
    return 0


def merge(args: argparse.Namespace) -> int:
    samples: dict[str, list[float]] = {}
    for path in args.inputs:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"::warning::跳过无法读取的耗时文件 {path}：{error}")
            continue
        if not isinstance(data, dict):
            continue
        for name, seconds in data.items():
            if isinstance(name, str) and isinstance(seconds, (int, float)) and 0 < seconds < MAX_SECONDS:
                samples.setdefault(name, []).append(float(seconds))
    if not samples:
        print("没有可合并的耗时数据")
        return 0
    merged = {name: max(1, round(statistics.median(values))) for name, values in sorted(samples.items())}
    text = json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":") if args.compact else (",", ": "))
    Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(f"合并 {len(args.inputs)} 份耗时，共 {len(merged)} 个用例，合计 {sum(merged.values())} 秒")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_extract = sub.add_parser("extract")
    p_extract.add_argument("--input", required=True)
    p_extract.add_argument("--output", required=True)
    p_merge = sub.add_parser("merge")
    p_merge.add_argument("--output", required=True)
    p_merge.add_argument("--compact", action="store_true", help="单行输出（用于 job 输出）")
    p_merge.add_argument("inputs", nargs="+")
    args = parser.parse_args()
    return extract(args) if args.command == "extract" else merge(args)


if __name__ == "__main__":
    sys.exit(main())
