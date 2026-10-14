"""System Agent 本地路由 golden set（纯离线，不调 LLM）。

WP-B16 路由 golden 回放闭环：本文件把 `golden_routes.json` 的既期望与
`scripts/capture-route-selected.py` 沉淀的 `route_selected` 事件对齐成同一套口径，
使"线上看到的 route_selected"可以直接离线重放、diff 与汇编回 golden。

事件字段与 `services/system_agent/runtime.py::next_event("route_selected", ...)` 一致：
`domains` / `route_source` / `route_reason`。这里额外保留 `text` 作为回放键。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.system_agent.tool_routing import (
    domain_catalog,
    register_dynamic_domain,
    route_locally,
)
from app.services.system_agent.turn_context import is_retry_reference

_GOLDEN = Path(__file__).resolve().parent / "data" / "golden_routes.json"
_REPLAY_DIR = Path(__file__).resolve().parent / "data" / "route_replay"
_CAPTURE_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "capture-route-selected.py"
_RUNTIME = Path(__file__).resolve().parents[2] / "app" / "services" / "system_agent" / "runtime.py"


def _ensure_plugin_sample_domain() -> set[str]:
    # 插件插槽样例域；每测前重注册，避免被其它用例覆盖。
    register_dynamic_domain(
        "plugin_demo_game",
        "彩票开奖与期次摘要",
        ("彩票", "开奖", "lottery", "奖池", "下注", "期号", "rounds"),
    )
    return set(domain_catalog().keys())


def _classify(text: str) -> str:
    if is_retry_reference(text):
        return "reference_or_retry"
    route = route_locally(
        text,
        available=_ensure_plugin_sample_domain(),
        memory_state={"last_domains": ["logs"]},
    )
    if route is None:
        return "model_route"
    if route.reason == "general_help":
        return "general_help"
    if route.reason == "no_live_system_data_needed":
        return "no_tools"
    if route.source == "memory":
        return "reference_or_domain"
    if route.domains:
        # 取首域匹配 expect domain
        return route.domains[0]
    return "no_tools"


def test_golden_routes_cover_at_least_40_cases() -> None:
    cases = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert len(cases) >= 40


def _known_domains() -> set[str]:
    """从 domain_catalog 动态取域集合。

    原先这里写死 17 个域，后来 catalog 扩到 37 个后没同步，新增域会被误判成
    "unknown expect"——看着像测试通过，其实断言根本没生效。按 catalog 取，
    新域一进来就必须在 golden json 里显式给期望。
    """
    return _ensure_plugin_sample_domain()


def test_golden_routes_match_expectations() -> None:
    cases = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    failures: list[str] = []
    for case in cases:
        text = case["text"]
        expect = case["expect"]
        got = _classify(text)
        domains = _known_domains()
        if expect in domains:
            if got != expect:
                failures.append(f"{text!r}: expect domain {expect}, got {got}")
        elif expect == "any_domain":
            if got not in domains and got not in {"reference_or_domain"}:
                failures.append(f"{text!r}: expect some domain, got {got}")
        elif expect == "general_help":
            if got != "general_help":
                failures.append(f"{text!r}: expect general_help, got {got}")
        elif expect == "no_tools":
            if got != "no_tools":
                failures.append(f"{text!r}: expect no_tools, got {got}")
        elif expect == "model_route":
            if got != "model_route":
                failures.append(f"{text!r}: expect model_route, got {got}")
        elif expect == "reference_or_retry":
            if got not in {"reference_or_retry", "reference_or_domain"}:
                failures.append(f"{text!r}: expect retry/ref, got {got}")
        elif expect == "reference_or_domain":
            if got not in {"reference_or_domain", "logs"}:
                failures.append(f"{text!r}: expect reference domain, got {got}")
        else:
            failures.append(f"unknown expect {expect} for {text!r}")
    assert not failures, "\n".join(failures)


# ─────────────────────────────────────────────────────
# WP-B16：route_selected 事件回放
# ─────────────────────────────────────────────────────
def _event_for(text: str, *, available: set[str] | None = None, memory_state: dict | None = None) -> dict:
    """把单条文本转成与运行时 `route_selected` 同字段的事件记录。"""
    domains = available if available is not None else _ensure_plugin_sample_domain()
    state = memory_state if memory_state is not None else {"last_domains": ["logs"]}
    if is_retry_reference(text):
        return {"text": text, "domains": [], "route_source": "memory", "route_reason": "retry_reference"}
    route = route_locally(text, available=domains, memory_state=state)
    if route is None:
        return {"text": text, "domains": [], "route_source": "model", "route_reason": "model_route"}
    return {
        "text": text,
        "domains": list(route.domains),
        "route_source": route.source,
        "route_reason": route.reason,
    }


def _load_replay_events() -> list[dict]:
    """读取 data/route_replay/*.jsonl 里的 route_selected 快照。"""
    events: list[dict] = []
    if not _REPLAY_DIR.exists():
        return events
    for path in sorted(_REPLAY_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            events.append(json.loads(line))
    return events


def test_route_selected_events_match_runtime_fields() -> None:
    """回放事件必须带齐运行时 route_selected 的字段名，且 domains 是字符串列表。"""
    events = _load_replay_events()
    assert events, "data/route_replay/*.jsonl 不能为空：否则回放闭环没有断言对象"
    for event in events:
        for field in ("text", "domains", "route_source", "route_reason"):
            assert field in event, f"route_selected 事件缺少字段 {field}: {event!r}"
        assert isinstance(event["domains"], list)
        assert all(isinstance(domain, str) for domain in event["domains"])
        assert event["route_source"] in {"local", "memory", "model"}


def test_recorded_route_selected_events_still_reproduce() -> None:
    """关键闭环：旧快照用当前 router 重放必须得到同样的事件。

    这是"误路由样本入库"的承接点——线上记下来的 route_selected 一旦 Router 语义变更，
    这里立刻失败，要么修 Router、要么显式更新快照，不会静默漂移。
    """
    failures: list[str] = []
    events = _load_replay_events()
    assert events, "data/route_replay/*.jsonl 不能为空"
    for event in events:
        text = event["text"]
        replay = _event_for(text)
        for field in ("domains", "route_source", "route_reason"):
            if replay[field] != event[field]:
                failures.append(
                    f"{text!r}: 记录 {field}={event[field]!r}，重放 {field}={replay[field]!r}"
                )
    assert not failures, "\n".join(failures)


def test_golden_cases_and_replay_events_are_consistent() -> None:
    """golden json 与回放快照覆盖同一批文本时，两者结论必须不冲突。"""
    cases = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    golden_texts = {str(case["text"]) for case in cases}
    replay_texts = {str(event["text"]) for event in _load_replay_events()}
    overlap = golden_texts & replay_texts
    assert overlap, "golden json 与回放快照没有交集，回放闭环无法相互校验"
    failures: list[str] = []
    for event in _load_replay_events():
        text = str(event["text"])
        if text not in golden_texts:
            continue
        case = next(case for case in cases if str(case["text"]) == text)
        classified = _classify(text)
        if case["expect"] in {"reference_or_retry", "reference_or_domain"}:
            # 这两类期望是"引用/重试语义"，与 domains 事件天然不同源，跳过 domains 比对
            continue
        if case["expect"] == "any_domain":
            if event["route_source"] == "model":
                failures.append(f"{text!r} golden 期望任一域，重放却走了模型路由")
        elif case["expect"] == "model_route":
            if event["route_source"] != "model":
                failures.append(f"{text!r} golden 期望模型路由，重放却没走")
        elif case["expect"] == "no_tools":
            if event["domains"]:
                failures.append(f"{text!r} golden 期望无工具，重放却给了域 {event['domains']}")
        elif classified == event["route_reason"] or classified == "model_route":
            # 分类与 reason 同名时直接对上；model_route 的 reason 由事件侧表达
            continue
        elif case["expect"] != classified and case["expect"] not in event["domains"]:
            failures.append(
                f"{text!r}: golden 期望 {case['expect']}，分类得到 {classified}，"
                f"回放事件 domains={event['domains']}，三方不一致"
            )
    assert not failures, "\n".join(failures)


def test_capture_script_produces_replayable_jsonl(tmp_path: Path) -> None:
    """capture 脚本必须真的能跑，且输出与测试同口径（防止脚本与测试各自演化）。"""
    assert _CAPTURE_SCRIPT.exists(), f"缺少 {_CAPTURE_SCRIPT}"
    import subprocess
    import sys as _sys

    output = tmp_path / "route_selected.jsonl"
    completed = subprocess.run(
        [
            _sys.executable,
            str(_CAPTURE_SCRIPT),
            "--from-golden",
            "--golden",
            str(_GOLDEN),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = [line for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == len(json.loads(_GOLDEN.read_text(encoding="utf-8")))
    for line in lines:
        event = json.loads(line)
        replay = _event_for(str(event["text"]))
        assert event["domains"] == replay["domains"], event["text"]
        assert event["route_source"] == replay["route_source"], event["text"]
        assert event["route_reason"] == replay["route_reason"], event["text"]


def test_runtime_route_selected_uses_the_same_field_names() -> None:
    """运行时 route_selected 的字段名必须与回放快照一致。

    只比字段名，不比值（tool_count / available_tool_count 依赖真实工具全集，离线取不到）。
    字段改名会让线上事件与离线回放对不上，排障时无法 diff，因此这里钉住契约。
    """
    source = _RUNTIME.read_text(encoding="utf-8")
    start = source.index('"route_selected"')
    # 从字符串参数之后的第一个 `(` 开始配平括号，否则会被 `list(route.domains)`
    # 的括号提前截断。`"route_selected"` 本身就是首个参数，前面还有一个调用括号。
    call_open = source.rindex("(", 0, start)
    depth = 0
    end = -1
    for index in range(call_open, len(source)):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    assert end > 0, "没有找到 route_selected 的 next_event 调用边界"
    block = source[start:end]
    events = _load_replay_events()
    assert events, "data/route_replay/*.jsonl 不能为空"
    for field in events[0]:
        if field == "text":
            # text 只是回放键，运行时事件不带它
            continue
        assert field in block, f"route_selected 事件字段 {field} 在 runtime.py 里找不到"
