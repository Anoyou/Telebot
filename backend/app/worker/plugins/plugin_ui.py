"""插件声明式 UI：控件契约与 ``ui.cards`` 卡片声明（Plugin API 1.3）。

规范来源是仓库根的 ``schemas/plugin-ui-widgets.json``，本目录的 ``plugin_ui_widgets.json`` 是
``scripts/sync-plugin-ui-widgets.py`` 生成的逐字节副本（Web 与 iOS 各有一份，CI 校验一致）。

卡片声明写在 ``plugin.json``::

    "reads": ["plugin.stats@1"],
    "ui": {"cards": [
        {"id": "health", "slot": "plugin.overview", "title": "最近 24 小时",
         "source": {"read": "plugin.stats@1"},
         "layout": {"type": "stats", "items": [{"label": "调用", "path": "totals.calls"}]}},
        {"id": "quota", "slot": "account.detail", "title": "配额",
         "source": {"action": "quota"}, "blocks": [{"type": "kv", "items": [...]}, {"type": "buttons", "items": [...]}]}
    ]}

- ``layout`` 是单块写法，``blocks`` 是多块（自上而下渲染）；块类型首批只有 stats / table / kv / list / buttons；
- ``source.read`` 必须是 ``reads`` 里声明过、且卡片上下文支持的资源；``source.action`` 调插件 ``on_ui_card``；
- 不合规的卡片整张忽略并给出 lint 警告，不影响其它卡片与插件加载。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from .host_read import normalize_resource_ref, validate_reads

CONTRACT_PATH = Path(__file__).with_name("plugin_ui_widgets.json")
CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
ACTION_ID_RE = CARD_ID_RE
HOST_CARD_PREFIX = "_host."


@lru_cache(maxsize=1)
def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def widget_ids() -> list[str]:
    return [str(item["id"]) for item in load_contract()["widgets"]]


def explicit_widget_values() -> set[str]:
    return {str(item["x_ui_widget"]) for item in load_contract()["widgets"] if item.get("x_ui_widget")}


def card_slots() -> list[str]:
    return [str(item["id"]) for item in load_contract()["cards"]["slots"]]


def card_layouts() -> list[str]:
    return [str(item["type"]) for item in load_contract()["cards"]["layouts"]]


def card_read_resources() -> list[str]:
    return [str(item) for item in load_contract()["cards"]["sources"]["read"]["resources"]]


def hook_timeout_seconds(hook: str) -> float:
    spec = load_contract()["hooks"].get(hook) or {}
    if hook in {"on_ui_card"}:
        return float(load_contract()["cards"]["sources"]["action"]["timeout_ms"]) / 1000
    return float(spec.get("timeout_ms") or 10000) / 1000


def localized_text(value: Any, default: str = "") -> str:
    """``"标题"`` 或 ``{"zh": "标题", "en": "Title"}`` → 中文文本。"""

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("zh", "zh-CN", "zh_CN", "en"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
        for text in value.values():
            if isinstance(text, str) and text.strip():
                return text.strip()
    return default


# ─────────────────────────────────────────────────────
# 配置 schema lint：未知控件
# ─────────────────────────────────────────────────────


def unknown_widgets_in_schema(schema: Any, prefix: str = "") -> list[str]:
    """返回 schema 里契约未列出的 ``x-ui-widget``（``字段路径: 值``）。"""

    known = explicit_widget_values()
    found: list[str] = []
    if not isinstance(schema, Mapping):
        return found
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for key, field in properties.items():
            if not isinstance(field, Mapping):
                continue
            path = f"{prefix}{key}"
            widget = field.get("x-ui-widget")
            if isinstance(widget, str) and widget.strip() and widget.strip() not in known:
                found.append(f"{path}: {widget.strip()}")
            found.extend(unknown_widgets_in_schema(field, prefix=f"{path}."))
            items = field.get("items")
            if isinstance(items, Mapping):
                found.extend(unknown_widgets_in_schema(items, prefix=f"{path}[]."))
    return found


# ─────────────────────────────────────────────────────
# 卡片声明
# ─────────────────────────────────────────────────────


def _block_problems(block: Any, index: int) -> list[str]:
    if not isinstance(block, Mapping):
        return [f"blocks[{index}] 必须是对象"]
    block_type = str(block.get("type") or "").strip()
    if block_type not in card_layouts():
        return [f"blocks[{index}].type={block_type!r} 不在首批布局 {card_layouts()} 内"]
    problems: list[str] = []
    if block_type in {"stats", "kv", "buttons"}:
        items = block.get("items")
        if block_type != "buttons" and items is not None and not isinstance(items, list):
            problems.append(f"blocks[{index}].items 必须是数组")
        if block_type == "buttons":
            if not isinstance(items, list) or not items:
                problems.append(f"blocks[{index}] buttons 需要非空 items")
            else:
                for pos, item in enumerate(items):
                    action_id = str(item.get("id") or "").strip() if isinstance(item, Mapping) else ""
                    if not ACTION_ID_RE.match(action_id):
                        problems.append(f"blocks[{index}].items[{pos}].id 不合法")
    if block_type == "table":
        columns = block.get("columns")
        if columns is not None and not isinstance(columns, list):
            problems.append(f"blocks[{index}].columns 必须是数组")
    return problems


def normalize_card(raw: Any, *, reads: Iterable[Any] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """校验并规范化一张卡片声明；不合规返回 ``(None, 原因)``。"""

    if not isinstance(raw, Mapping):
        return None, ["卡片必须是对象"]
    card_id = str(raw.get("id") or "").strip()
    label = card_id or "?"
    problems: list[str] = []
    if not CARD_ID_RE.match(card_id) or card_id.startswith(HOST_CARD_PREFIX):
        problems.append("id 只能由字母数字与 _.- 组成（≤48 字符，不能以 _host. 开头）")
    slot = str(raw.get("slot") or "").strip()
    if slot not in card_slots():
        problems.append(f"slot={slot!r} 不在 {card_slots()} 内")
    source = raw.get("source")
    normalized_source: dict[str, Any] = {}
    if not isinstance(source, Mapping):
        problems.append("缺少 source（{\"read\": \"资源@版本\"} 或 {\"action\": \"名称\"}）")
    elif source.get("read") is not None:
        ref = normalize_resource_ref(source.get("read"))
        declared = set(validate_reads(reads)[0])
        if ref not in card_read_resources():
            problems.append(f"source.read={ref!r} 不是卡片可读资源 {card_read_resources()}")
        elif ref not in declared:
            problems.append(f"source.read={ref!r} 没有在 plugin.json reads 里声明")
        normalized_source = {"read": ref}
    elif "action" in source:
        action = str(source.get("action") or card_id).strip()
        normalized_source = {"action": action}
    else:
        problems.append("source 只支持 read 或 action")
    raw_blocks = raw.get("blocks")
    if raw_blocks is None and raw.get("layout") is not None:
        raw_blocks = [raw.get("layout")]
    if not isinstance(raw_blocks, list) or not raw_blocks:
        problems.append("缺少 layout / blocks")
        raw_blocks = []
    for index, block in enumerate(raw_blocks):
        problems.extend(_block_problems(block, index))
    if problems:
        return None, [f"ui.cards[{label}]: {item}" for item in problems]
    refresh = raw.get("refresh_seconds")
    try:
        refresh_seconds = max(30, int(refresh)) if refresh not in (None, "", 0) else None
    except (TypeError, ValueError):
        refresh_seconds = None
    card = {
        "id": card_id,
        "slot": slot,
        "title": localized_text(raw.get("title"), card_id),
        "subtitle": localized_text(raw.get("subtitle")) or None,
        "source": normalized_source,
        "blocks": [dict(block) for block in raw_blocks],
        "refresh_seconds": refresh_seconds,
    }
    return card, []


def normalize_ui_cards(ui: Any, *, reads: Iterable[Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """``plugin.json`` 的 ``ui`` → ``(合规卡片, lint 警告)``。"""

    if ui is None:
        return [], []
    if not isinstance(ui, Mapping):
        return [], ["ui 必须是对象"]
    raw_cards = ui.get("cards")
    if raw_cards is None:
        return [], []
    if not isinstance(raw_cards, list):
        return [], ["ui.cards 必须是数组"]
    cards: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw in raw_cards:
        card, problems = normalize_card(raw, reads=reads)
        if card is None:
            warnings.extend(problems)
            continue
        if card["id"] in seen:
            warnings.append(f"ui.cards[{card['id']}]: id 重复，后一张被忽略")
            continue
        seen.add(card["id"])
        cards.append(card)
    return cards, warnings


def lint_ui_declaration(manifest: Mapping[str, Any]) -> list[str]:
    """插件元数据 lint：卡片声明与未知控件。"""

    warnings = list(normalize_ui_cards(manifest.get("ui"), reads=manifest.get("reads"))[1])
    schema = manifest.get("config_schema")
    for item in unknown_widgets_in_schema(schema):
        warnings.append(f"config_schema 使用了契约未列出的控件 {item}（客户端会只读显示「此控件需要更新 App」）")
    warnings.extend(lint_rules_declaration(schema))
    return warnings


DRY_RUN_SAMPLE_FIELDS = ("message", "chat_type", "chat_id")


def rule_schema_of(schema: Any) -> Mapping[str, Any] | None:
    """``x-ui-mode=rules`` 时每条规则的 schema：``x-rule-schema``，省略时整个 config_schema。"""

    if not isinstance(schema, Mapping) or schema.get("x-ui-mode") != "rules":
        return None
    declared = schema.get("x-rule-schema")
    if isinstance(declared, Mapping):
        return declared
    return schema


def lint_rules_declaration(schema: Any) -> list[str]:
    """通用规则页声明（x-rule-schema / x-rule-summary / x-dry-run / x-ui-visible-if）的 lint。"""

    warnings: list[str] = []
    if not isinstance(schema, Mapping):
        return warnings
    declared = schema.get("x-rule-schema")
    if declared is not None:
        if schema.get("x-ui-mode") != "rules":
            warnings.append("config_schema.x-rule-schema 只在 x-ui-mode=rules 时生效")
        if not isinstance(declared, Mapping) or not isinstance(declared.get("properties"), Mapping):
            warnings.append("config_schema.x-rule-schema 必须是带 properties 的对象 schema")
            declared = None
        else:
            for item in unknown_widgets_in_schema(declared, prefix="x-rule-schema."):
                warnings.append(f"config_schema 使用了契约未列出的控件 {item}（客户端会只读显示「此控件需要更新 App」）")
    rule_schema = rule_schema_of(schema)
    rule_props = rule_schema.get("properties") if isinstance(rule_schema, Mapping) else None
    rule_props = rule_props if isinstance(rule_props, Mapping) else {}
    summary = schema.get("x-rule-summary")
    if summary is not None:
        if not isinstance(summary, list) or not all(isinstance(item, str) for item in summary):
            warnings.append("config_schema.x-rule-summary 必须是字段名数组")
        else:
            missing = [item for item in summary if item not in rule_props]
            if missing:
                warnings.append(f"config_schema.x-rule-summary 引用了规则 schema 里没有的字段：{', '.join(missing)}")
    dry_run = schema.get("x-dry-run")
    if dry_run is not None and dry_run is not False:
        if not isinstance(dry_run, Mapping):
            warnings.append("config_schema.x-dry-run 必须是对象或 false")
        else:
            fields = dry_run.get("sample_fields")
            if fields is not None and (
                not isinstance(fields, list) or any(item not in DRY_RUN_SAMPLE_FIELDS for item in fields)
            ):
                warnings.append("config_schema.x-dry-run.sample_fields 只能取 message / chat_type / chat_id")
    for scope_name, props in (("config_schema", schema.get("properties")), ("x-rule-schema", rule_props)):
        if not isinstance(props, Mapping):
            continue
        for key, field in props.items():
            cond = field.get("x-ui-visible-if") if isinstance(field, Mapping) else None
            if cond is None:
                continue
            if not isinstance(cond, Mapping) or not isinstance(cond.get("field"), str):
                warnings.append(f"{scope_name}.{key}.x-ui-visible-if 必须是 {{field, equals|in|not_in}}")
            elif cond["field"] not in props:
                warnings.append(f"{scope_name}.{key}.x-ui-visible-if 引用了同级不存在的字段 {cond['field']}")
    return warnings


__all__ = [
    "CONTRACT_PATH",
    "HOST_CARD_PREFIX",
    "card_layouts",
    "card_read_resources",
    "card_slots",
    "explicit_widget_values",
    "hook_timeout_seconds",
    "lint_rules_declaration",
    "lint_ui_declaration",
    "load_contract",
    "localized_text",
    "normalize_card",
    "rule_schema_of",
    "normalize_ui_cards",
    "unknown_widgets_in_schema",
    "widget_ids",
]
