#!/usr/bin/env python3
"""把 System Agent 的 ``route_selected`` 事件沉淀成可回放的 jsonl。

用途（WP-B16）：``route_selected`` 只在真实运行时出现（``runtime.py`` 的 ``next_event``），
事后排障能看到"当时为什么走了这些域"，但无法在离线环境里重放对比。本脚本把一批文本喂给
``route_locally``，产出与运行时事件同字段的记录，按 jsonl 一行一条输出，方便直接 diff、
汇编样本或类推。只读纯函数、不连数据库、不调 LLM，可在 CI 与本地随时执行：

    backend/.venv/bin/python scripts/capture-route-selected.py \
        --input samples.txt --output route_selected.jsonl
    backend/.venv/bin/python scripts/capture-route-selected.py \
        --from-golden --output route_selected.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# 必须用本仓库的 backend/app：原先判断的是 REPO_ROOT 却插入 REPO_ROOT/backend，
# PYTHONPATH 含仓库根（CI / pytest 的常见设置）时就不插入，``app`` 会落到 venv
# 里可编辑安装指向的另一个检出（例如别的 worktree 的旧代码），回放结果随之漂移。
_BACKEND_DIR = str(REPO_ROOT / "backend")
if _BACKEND_DIR in sys.path:
    sys.path.remove(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)

from app.services.system_agent.tool_routing import (  # noqa: E402
    domain_catalog,
    register_dynamic_domain,
    route_locally,
)
from app.services.system_agent.turn_context import is_retry_reference  # noqa: E402


def plugin_sample_domains() -> set[str]:
    """与 golden 测试一致：注册插件插槽样例域，保证同一文本得到同一路由。"""
    register_dynamic_domain(
        "plugin_demo_game",
        "彩票开奖与期次摘要",
        ("彩票", "开奖", "lottery", "奖池", "下注", "期号", "rounds"),
    )
    return set(domain_catalog().keys())


def route_selected_event(text: str, *, available: set[str], memory_state: dict | None = None) -> dict:
    """产出与 ``runtime.py`` 中 ``route_selected`` 同字段的事件记录。"""
    if is_retry_reference(text):
        return {
            "text": text,
            "domains": [],
            "route_source": "memory",
            "route_reason": "retry_reference",
        }
    route = route_locally(text, available=available, memory_state=memory_state)
    if route is None:
        return {
            "text": text,
            "domains": [],
            "route_source": "model",
            "route_reason": "model_route",
        }
    return {
        "text": text,
        "domains": list(route.domains),
        "route_source": route.source,
        "route_reason": route.reason,
    }


def _read_texts(path: Path) -> list[str]:
    texts = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        # 支持 # 注释与空行，方便把排障样本直接存档成文件
        if not stripped or stripped.startswith("#"):
            continue
        texts.append(stripped)
    return texts


def _from_golden(path: Path) -> list[str]:
    return [str(case["text"]) for case in json.loads(path.read_text(encoding="utf-8"))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="capture route_selected events as replayable jsonl")
    parser.add_argument("--input", type=Path, help="pure text sample file; lines starting with # are comments")
    parser.add_argument("--from-golden", action="store_true", help="take samples from golden_routes.json")
    parser.add_argument(
        "--golden",
        type=Path,
        default=REPO_ROOT / "backend/app/tests/data/golden_routes.json",
        help="golden json path used by --from-golden",
    )
    parser.add_argument("--output", type=Path, required=True, help="output jsonl path")
    parser.add_argument(
        "--memory-state",
        default='{"last_domains": ["logs"]}',
        help="memory_state JSON injected into routing; defaults to the golden test convention",
    )
    args = parser.parse_args(argv)

    if not args.input and not args.from_golden:
        parser.error("choose exactly one source: --input <file> or --from-golden")
    if args.input and args.from_golden:
        parser.error("--input and --from-golden are mutually exclusive")

    texts = _from_golden(args.golden) if args.from_golden else _read_texts(args.input)
    if not texts:
        parser.error("no samples were collected")

    try:
        memory_state = json.loads(args.memory_state)
    except json.JSONDecodeError as exc:
        parser.error(f"--memory-state is not valid JSON: {exc}")

    available = plugin_sample_domains()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for text in texts:
            event = route_selected_event(text, available=available, memory_state=memory_state)
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(texts)} route_selected events -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
