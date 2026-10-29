"""插件稳定契约（Plugin Contract）：把宿主对插件承诺的接口面整理成机器可读快照。

为什么单独有一个“契约版本”
--------------------------

``PLUGIN_API_VERSION``（见 :mod:`.versioning`）从 0.107.0 的 1.0 一路递增到 1.6，已发布插件在
``plugin.json`` 里写的是 ``"plugin_api": "^1.0"`` / ``">=1.3"`` 之类的范围。若把它“重置”成 1.0，
声明了 ``>=1.3`` 的插件会被判不兼容。所以：

- ``PLUGIN_API_VERSION`` **继续单调递增**（新增能力 MINOR + 1，破坏兼容才 MAJOR + 1），插件的
  ``plugin_api`` 判断方式完全不变；
- 另立 :data:`PLUGIN_CONTRACT_VERSION` = ``"1.0"``，表示“稳定契约 1.0”：以 Plugin API
  :data:`CONTRACT_BASELINE_PLUGIN_API`（1.6）时的接口面为基线冻结。契约 1.x 期间只允许**新增**
  （新钩子、新可选参数、新 manifest 字段、新能力），不允许删除或改变已冻结的条目；
- 冻结基线保存在 ``schemas/plugin-api-contract-1.0.frozen.json``（永不修改），当前接口面由
  :func:`build_contract` 生成到 ``schemas/plugin-api-contract.json``；契约测试用
  :func:`breaking_changes` 检查“基线 ⊆ 当前”，CI 拦截任何破坏性改动。

本模块只做内省与比较，不改变任何运行时行为。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .versioning import PLUGIN_API_VERSION

# 稳定契约版本（与 PLUGIN_API_VERSION 相互独立，见模块说明）。
PLUGIN_CONTRACT_VERSION = "1.0"
# 契约 1.0 冻结时的 Plugin API 版本：这一版的接口面就是基线。
CONTRACT_BASELINE_PLUGIN_API = "1.6"
# 宿主开始承诺契约 1.0 的版本；合并时随版本号顺延（只影响文档与快照里的说明字段）。
CONTRACT_SINCE_HOST = "0.112.0-beta.4"

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPO_ROOT / "schemas" / "plugin-api-contract.json"
FROZEN_BASELINE_PATH = REPO_ROOT / "schemas" / "plugin-api-contract-1.0.frozen.json"
MANIFEST_SCHEMA_PATH = REPO_ROOT / "schemas" / "plugin.schema.json"
CONTRACT_SCHEMA_PATH = REPO_ROOT / "schemas" / "plugin-contract.schema.json"

# ─────────────────────────────────────────────────────
# 钩子清单
# ─────────────────────────────────────────────────────
# 插件类方法钩子从 base.Plugin 内省签名；这里补充“在哪里调用、时限、引入版本”等说明。
# 模块级函数钩子与 manifest 声明的处理函数没有基类可内省，签名在这里登记。
_PLUGIN_METHOD_HOOKS: dict[str, dict[str, Any]] = {
    "on_startup": {"since": "0.33.0", "timeout_s": None, "summary": "插件实例启动（每个账号 worker 一次）"},
    "on_shutdown": {"since": "0.33.0", "timeout_s": None, "summary": "插件实例停止 / 热重载前"},
    "on_message": {"since": "0.33.0", "timeout_s": None, "summary": "UserBot 新消息（message_channels 决定收哪些）"},
    "on_message_edited": {"since": "0.33.0", "timeout_s": None, "summary": "UserBot 消息被编辑"},
    "on_direct_message": {"since": "0.46.0", "timeout_s": None, "summary": "交互 Bot 直通入口（capabilities.telegram_direct_passthrough）"},
    "on_command": {"since": "0.33.0", "timeout_s": None, "summary": "命令（commands / SDK 简单命令）"},
    "on_interaction": {"since": "0.40.0", "timeout_s": None, "summary": "交互入口迁移桥（interaction_entries）；新插件优先 on_event"},
    "on_event": {"since": "0.40.0", "timeout_s": None, "summary": "Event Bus 事件（event_subscriptions），返回标准动作列表"},
    "on_config_action": {"since": "0.45.0", "timeout_s": None, "summary": "配置页动作按钮（config_actions）"},
    "on_config_validate": {"since": "0.110.0-beta.1", "timeout_s": 2.0, "summary": "保存配置前校验（fail-open）"},
    "on_config_saved": {"since": "0.110.0-beta.1", "timeout_s": None, "summary": "配置保存后通知（后台执行）"},
    "on_ui_card": {"since": "0.110.0-beta.1", "timeout_s": 3.0, "summary": "声明式卡片数据（ui.cards source=action）"},
    "on_ui_action": {"since": "0.110.0-beta.1", "timeout_s": 10.0, "summary": "卡片按钮 / 菜单项 / 页面 Bridge action.invoke"},
}

_DECLARED_HOOKS: tuple[dict[str, Any], ...] = (
    {
        "name": "dry_run_rule",
        "kind": "module_function",
        "params": [{"name": "config", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False},
                   {"name": "sample", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False}],
        "async": "optional",
        "returns": "dict | (matched, output)",
        "timeout_s": 3.0,
        "since": "0.110.0-beta.2",
        "summary": "规则试运行（无副作用）；plugin.py 或包 __init__ 导出",
    },
    {
        "name": "parse_payment_notice",
        "kind": "module_function",
        "params": [{"name": "text", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False},
                   {"name": "meta", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False}],
        "async": "optional",
        "returns": "dict | None",
        "timeout_s": 1.0,
        "since": "0.111.0-beta.1",
        "summary": "转账通知解析（plugin.json payment_sources）；只返回解析结果，结算仍由宿主执行",
    },
    {
        "name": "<middleware handler>",
        "kind": "manifest_handler",
        "params": [{"name": "self", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False},
                   {"name": "ctx", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False},
                   {"name": "value", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False},
                   {"name": "mw", "kind": "POSITIONAL_OR_KEYWORD", "has_default": False}],
        "async": "required",
        "returns": "修改后的 value / None（不改）/ mw.drop(...)",
        "timeout_s": "manifest middleware[].timeout_ms（50–2000，默认 250）",
        "since": "0.109.0-beta.1",
        "summary": "消息管线中间件（plugin.json middleware[].handler，默认 on_inbound / on_outbound / on_delivered）",
    },
)


def _param_list(func: Callable[..., Any]) -> list[dict[str, Any]]:
    sig = inspect.signature(func)
    return [
        {
            "name": p.name,
            "kind": p.kind.name,
            "has_default": p.default is not inspect.Parameter.empty,
        }
        for p in sig.parameters.values()
    ]


def _hooks() -> list[dict[str, Any]]:
    from .base import Plugin

    out: list[dict[str, Any]] = []
    for name, meta in _PLUGIN_METHOD_HOOKS.items():
        func = getattr(Plugin, name)
        out.append(
            {
                "name": name,
                "kind": "plugin_method",
                "params": _param_list(func),
                "async": "required" if inspect.iscoroutinefunction(func) else "no",
                "timeout_s": meta["timeout_s"],
                "since": meta["since"],
                "summary": meta["summary"],
            }
        )
    out.extend(dict(item) for item in _DECLARED_HOOKS)
    return out


def _public_methods(cls: type) -> list[dict[str, Any]]:
    methods: list[dict[str, Any]] = []
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        raw = inspect.getattr_static(cls, name, None)
        if isinstance(raw, property):
            methods.append({"name": name, "kind": "property"})
            continue
        if isinstance(raw, (staticmethod, classmethod)):
            raw = raw.__func__
        if not callable(raw) or isinstance(raw, type):
            continue
        try:
            params = _param_list(raw)
        except (TypeError, ValueError):  # pragma: no cover - C 实现
            continue
        methods.append(
            {
                "name": name,
                "kind": "async" if inspect.iscoroutinefunction(raw) or inspect.isasyncgenfunction(raw) else "sync",
                "params": params,
            }
        )
    return methods


def _facade_classes() -> dict[str, type]:
    from ..scheduler_runtime import SchedulerFacade
    from . import loader
    from .ai_facade import PluginAI
    from .base import PluginIdentityFacade
    from .capabilities import PluginCapabilities
    from .host_read import PluginHost
    from .http_facade import PluginHTTP
    from .message_ops import BufferedMessageOps
    from .skills_facade import PluginSkills
    from .storage import PluginStorage, ReadOnlyPluginStorage
    from .stream_edit import PluginStreamEditor

    return {
        "ctx.messages[buffered]": BufferedMessageOps,
        "ctx.messages[live]": loader._LiveMessageOps,
        "ctx.messages[entry]": loader._InteractionEntryMessageOps,
        "ctx.messages.stream_editor()": PluginStreamEditor,
        "ctx.ai": PluginAI,
        "ctx.http": PluginHTTP,
        "ctx.storage": PluginStorage,
        "ctx.storage[readonly]": ReadOnlyPluginStorage,
        "ctx.skills": PluginSkills,
        "ctx.scheduler": SchedulerFacade,
        "ctx.host": PluginHost,
        "ctx.identities": PluginIdentityFacade,
        "ctx.capabilities": PluginCapabilities,
    }


def _context() -> dict[str, Any]:
    from .base import PluginContext

    fields = [f.name for f in dataclasses.fields(PluginContext)]
    members = [item for item in _public_methods(PluginContext) if item["name"] not in fields]
    return {"fields": fields, "members": members}


def _capability_snapshot() -> dict[str, Any]:
    from . import loader
    from .capabilities import CAPABILITIES, PluginCapabilities
    from .message_ops import BufferedMessageOps

    registry = [
        {
            "name": item.name,
            "version": item.version,
            "since": item.since,
            "plugin_api": item.plugin_api,
            "legacy_attr": item.legacy_attr,
            "facades": sorted(item.facades),
            "deprecated_in": item.deprecated_in,
            "removed_in": item.removed_in,
        }
        for item in CAPABILITIES
    ]
    matrix: dict[str, list[str]] = {}
    legacy: dict[str, list[str]] = {}
    for label, cls in (
        ("no_messages", None),
        ("buffered", BufferedMessageOps),
        ("live", loader._LiveMessageOps),
        ("entry", loader._InteractionEntryMessageOps),
    ):
        stub = object.__new__(cls) if cls is not None else None
        matrix[label] = sorted(PluginCapabilities(stub).names())
        if cls is not None:
            legacy[label] = sorted(
                item.legacy_attr
                for item in CAPABILITIES
                if item.legacy_attr and getattr(cls, item.legacy_attr, False) is True
            )
    return {"registry": registry, "ctx_capabilities_has": matrix, "legacy_attrs_true": legacy}


def _resolve(schema: Mapping[str, Any], defs: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return defs.get(ref.rsplit("/", 1)[-1], {})
    return schema


def _types(schema: Mapping[str, Any], defs: Mapping[str, Any]) -> list[str]:
    schema = _resolve(schema, defs)
    found: set[str] = set()
    raw = schema.get("type")
    if isinstance(raw, str):
        found.add(raw)
    elif isinstance(raw, list):
        found.update(str(item) for item in raw)
    for key in ("anyOf", "oneOf"):
        for sub in schema.get(key) or []:
            if isinstance(sub, Mapping):
                found.update(_types(sub, defs))
    if not found and ("properties" in schema or "additionalProperties" in schema):
        found.add("object")
    if not found:
        found.add("any")
    return sorted(found)


def _manifest_fields(schema: Mapping[str, Any]) -> dict[str, list[str]]:
    defs = schema.get("$defs") or {}
    out: dict[str, list[str]] = {}

    def walk(node: Mapping[str, Any], prefix: str, depth: int) -> None:
        if depth > 6:
            return
        node = _resolve(node, defs)
        variants = [node] + [
            _resolve(sub, defs) for key in ("anyOf", "oneOf") for sub in node.get(key) or [] if isinstance(sub, Mapping)
        ]
        for variant in variants:
            for name, sub in (variant.get("properties") or {}).items():
                if not isinstance(sub, Mapping):
                    continue
                path = f"{prefix}{name}"
                out[path] = sorted(set(out.get(path, [])) | set(_types(sub, defs)))
                walk(sub, f"{path}.", depth + 1)
            items = variant.get("items")
            if isinstance(items, Mapping):
                walk(items, prefix.rstrip(".") + "[].", depth + 1)

    walk(schema, "", 0)
    return dict(sorted(out.items()))


def _manifest() -> dict[str, Any]:
    install_schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    return {
        "install_schema": "schemas/plugin.schema.json",
        "contract_schema": "schemas/plugin-contract.schema.json",
        "install_fields": sorted((install_schema.get("properties") or {}).keys()),
        "fields": _manifest_fields(build_manifest_schema()),
    }


def _ui() -> dict[str, Any]:
    from .plugin_ui import load_contract

    data = load_contract()
    pages = data.get("pages") or {}
    return {
        "widgets_contract": "schemas/plugin-ui-widgets.json",
        "widgets_contract_version": data.get("contract_version"),
        "widgets": sorted(str(item["id"]) for item in data.get("widgets") or []),
        "schema_keys": sorted(str(item["key"]) for item in data.get("keys") or []),
        "card_slots": sorted(str(item["id"]) for item in data["cards"]["slots"]),
        "card_layouts": sorted(str(item["type"]) for item in data["cards"]["layouts"]),
        "card_read_resources": sorted(str(item) for item in data["cards"]["sources"]["read"]["resources"]),
        "ui_hooks": {name: (spec or {}).get("timeout_ms") for name, spec in sorted((data.get("hooks") or {}).items())},
        "page_menus": sorted(str(item["id"]) for item in pages.get("menus") or []),
        "page_fields": sorted(pages.get("page_fields") or {}),
        "action_fields": sorted(pages.get("action_fields") or {}),
    }


def _bridge() -> dict[str, Any]:
    from ...services import plugin_ui_pages as pages

    methods: dict[str, str] = {name: "always" for name in sorted(pages._ALWAYS_METHODS)}
    # theme.get 由 Web / iOS 宿主在本地应答（不经服务器），任何页面都可调用。
    methods["theme.get"] = "always (client)"
    methods.update({name: f"bridge:{perm}" for name, perm in sorted(pages._PERMISSION_METHODS.items())})
    return {
        "protocol": pages.BRIDGE_PROTOCOL,
        "menus": list(pages.PAGE_MENUS),
        "methods": dict(sorted(methods.items())),
        "js_api": ["action", "call", "card", "config", "onTheme", "protocol", "ready", "theme"],
    }


def _events() -> dict[str, Any]:
    from ...services import event_bus

    return {
        "sources": sorted(event_bus.VALID_EVENT_SOURCES),
        "types": sorted(event_bus.VALID_EVENT_TYPES),
        "scopes": sorted(event_bus.VALID_EVENT_SCOPES),
        "filter_keys": sorted(event_bus.SUPPORTED_FILTER_KEYS),
    }


def _actions() -> list[str]:
    from ...services.interaction.action_core import CANONICAL_ACTION_TYPES

    return sorted(CANONICAL_ACTION_TYPES)


def _extension_points() -> dict[str, Any]:
    from ...services import payment_sources
    from . import extension_points, middleware

    return {
        "provides": sorted(extension_points.KNOWN_PROVIDES) if hasattr(extension_points, "KNOWN_PROVIDES") else [extension_points.AI_IMAGE_BACKEND],
        "payment_sources": {
            "manifest_key": payment_sources.MANIFEST_KEY,
            "hook": payment_sources.HOOK,
            "timeout_s": payment_sources.PARSE_TIMEOUT_SECONDS,
            "settlement_fields": list(payment_sources.SETTLEMENT_FIELDS),
        },
        "middleware": {
            "stages": sorted(getattr(middleware, "STAGES", ("inbound", "outbound", "delivered"))),
            "timeout_ms": {
                "default": middleware.DEFAULT_TIMEOUT_MS,
                "min": middleware.MIN_TIMEOUT_MS,
                "max": middleware.MAX_TIMEOUT_MS,
            },
        },
    }


def _permissions() -> list[str]:
    from . import loader, middleware, sandbox

    known: set[str] = set(sandbox.ALLOWED_API) | set(sandbox._NON_CLIENT_PERMISSIONS)
    known |= set(loader._SUPPORTED_FACADE_PERMISSIONS)
    known.add(middleware.MIDDLEWARE_PERMISSION)
    return sorted(known)


def _host_read() -> list[str]:
    from .host_read import HOST_RESOURCES

    return sorted(item.ref for item in HOST_RESOURCES)


def _deprecations() -> list[str]:
    from .deprecations import DEPRECATIONS

    return sorted(item.id for item in DEPRECATIONS)


# ─────────────────────────────────────────────────────
# plugin.json 详细 Schema（契约文档的机器可读版本）
# ─────────────────────────────────────────────────────
# schemas/plugin.schema.json 由 Pydantic 安装模型导出，嵌套字段只写到“对象 / 数组”；这里按宿主
# 实际读取的字段补全一层（枚举直接取自运行时常量）。所有对象都 additionalProperties: true，
# 新宿主加字段不会让旧 Schema 报错；插件写错类型或枚举值会被指出。
_LOCALIZED = {"anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": {"type": "string"}}]}
_ID48 = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$"}


def _obj(properties: Mapping[str, Any], required: Iterable[str] = (), **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "additionalProperties": True, "properties": dict(properties)}
    req = list(required)
    if req:
        out["required"] = req
    out.update(extra)
    return out


def _str_or_list(enum: Iterable[str] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "string"}
    if enum is not None:
        item["enum"] = sorted(enum)
    return {"anyOf": [item, {"type": "array", "items": item}]}


def build_manifest_schema() -> dict[str, Any]:
    from ...services import account_bot_service as abs_
    from ...services import event_bus
    from ...services import plugin_ui_pages as pages
    from . import middleware
    from .plugin_ui import load_contract

    ui_contract = load_contract()
    slots = sorted(str(item["id"]) for item in ui_contract["cards"]["slots"])
    layouts = sorted(str(item["type"]) for item in ui_contract["cards"]["layouts"])
    bridge_perms = sorted(set(pages._PERMISSION_METHODS.values()))
    scope_enum = sorted(abs_.VALID_CONCURRENCY)
    participant_enum = sorted(abs_.VALID_INTERACTION_PARTICIPANT_POLICIES)

    entry_policy = _obj(
        {
            "session_scope": {"type": "string", "enum": scope_enum},
            "participant_policy": {"type": "string", "enum": participant_enum},
            "prize": {"type": "boolean"},
            "default_start_keywords": {"type": "array", "items": {"type": "string"}},
        },
        description="入口参与方式与会话边界（Plugin API 1.5，0.112.0-beta.2+）；任一入口声明后宿主不再查兜底入口表",
    )
    legacy_entry_key = _obj(
        {
            "key": {"type": "string", "minLength": 1},
            "session_scope": {"type": "string", "enum": scope_enum},
            "participant_policy": {"type": "string", "enum": participant_enum},
            "prize": {"type": "boolean"},
        },
        required=["key"],
    )
    interaction_entry = _obj(
        {
            "key": {"type": "string", "minLength": 1},
            "title": _LOCALIZED,
            "description": {"type": "string"},
            "interaction_profile": {"type": "string"},
            # 宿主认识 bridge / direct / hybrid，其它值按 bridge 处理（不报错）。
            "launch_mode": {"type": "string", "examples": sorted(abs_.VALID_INTERACTION_LAUNCH_MODES)},
            "dispatch_modes": {"type": "array", "items": {"type": "string"}},
            "events": {"type": "array", "items": {"type": "string", "enum": sorted(event_bus.VALID_EVENT_TYPES)}},
            "session_scope": {"type": "string", "enum": scope_enum},
            "session_policy": {"type": "object"},
            "participant_policy": {"type": "string", "enum": participant_enum},
            "payload_contract": {"type": "object"},
            "result_contract": {"type": "object"},
            "settlement": {"type": ["object", "boolean", "string", "null"]},
            "message_channels": {"type": ["object", "array"], "description": "{触发方式: 发送通道} 映射"},
            "preserve_command_trigger": {"type": "boolean"},
            "command_fallback": {"type": ["object", "boolean", "string", "array", "null"]},
            "input_schema": {"type": "object"},
            "policy": {"type": "object"},
            "entry_policy": entry_policy,
            "legacy_entry_keys": {
                "type": "array",
                "items": legacy_entry_key,
                "description": "旧入口 key 声明（Plugin API 1.6，0.112.0-beta.3+）；不算正式入口",
            },
        },
        required=["key"],
    )
    event_subscription = _obj(
        {
            "events": {"type": "array", "items": {"type": "string", "enum": sorted(event_bus.VALID_EVENT_TYPES)}},
            "source": _str_or_list(event_bus.VALID_EVENT_SOURCES),
            "scope": {"type": "string", "enum": sorted(event_bus.VALID_EVENT_SCOPES)},
            "entry_key": {"type": "string"},
            "filters": {"type": "object"},
            "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
            "description": {"type": "string"},
        },
    )
    middleware_item = _obj(
        {
            "stage": {"type": "string", "enum": sorted(getattr(middleware, "STAGES", ("inbound", "outbound", "delivered")))},
            "handler": {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$"},
            "order": {"type": "integer"},
            "timeout_ms": {"type": "integer", "minimum": middleware.MIN_TIMEOUT_MS, "maximum": middleware.MAX_TIMEOUT_MS},
            "critical": {"type": "boolean"},
            "match": {"type": "object"},
        },
        required=["stage"],
    )
    payment_source = _obj(
        {"id": {"type": "string", "minLength": 1}, "title": _LOCALIZED, "description": {"type": "string"}},
        required=["id"],
    )
    card = _obj(
        {
            "id": _ID48,
            "slot": {"type": "string", "enum": slots},
            "title": _LOCALIZED,
            "subtitle": _LOCALIZED,
            "source": _obj({"read": {"type": "string"}, "action": {"type": "string"}}),
            "layout": _obj({"type": {"type": "string", "enum": layouts}}, required=["type"]),
            "blocks": {"type": "array", "items": _obj({"type": {"type": "string", "enum": layouts}}, required=["type"])},
            "refresh_seconds": {"type": ["integer", "null"]},
        },
        required=["id", "slot", "source"],
    )
    page = _obj(
        {
            "id": _ID48,
            "title": _LOCALIZED,
            "subtitle": _LOCALIZED,
            "file": {"type": "string", "pattern": r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}\.html?$"},
            "menus": {"type": "array", "items": {"type": "string", "enum": list(pages.PAGE_MENUS)}},
            "bridge": {"type": "array", "items": {"type": "string", "enum": bridge_perms}},
            "actions": {"type": "array", "items": {"type": "string"}},
        },
        required=["id", "file"],
    )
    action = _obj(
        {
            "id": _ID48,
            "menu": {"type": "string", "enum": list(pages.PAGE_MENUS)},
            "title": _LOCALIZED,
            "confirm": {"type": ["boolean", "string"]},
            "style": {"type": "string"},
            "page": {"type": "string"},
        },
        required=["id"],
    )
    ui = _obj(
        {
            "cards": {"type": "array", "items": card},
            "pages": {"type": "array", "items": page},
            "actions": {"type": "array", "items": action},
            "rule_editor": _obj({"entries": {"type": "object"}}),
        }
    )
    config_action = _obj(
        {
            "key": {"type": "string", "minLength": 1},
            "title": _LOCALIZED,
            "description": {"type": "string"},
            "placement": {"type": "string"},
            "submit_label": {"type": "string"},
            "input_schema": {"type": "object"},
        },
        required=["key"],
    )
    properties: dict[str, Any] = {
        "name": {"type": "string", "description": "插件 key（与 key 二选一，name 优先）"},
        "key": {"type": "string"},
        "display_name": {"type": "string"},
        "description": {"type": "string"},
        "author": {"type": "string"},
        "version": {"type": "string"},
        "entry": {"type": "string"},
        "category": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "usage": {"type": ["string", "null"]},
        "min_telepilot_version": {"type": ["string", "null"]},
        "min_telebot_version": {"type": ["string", "null"], "description": "已弃用的旧名，仍兼容"},
        "plugin_api": {"type": ["string", "null"], "description": "Plugin API 兼容范围，如 ^1.0 / >=1.3,<2"},
        "permissions": {"type": "array", "items": {"type": "string"}},
        "allowed_hosts": {"type": "array", "items": {"type": "string"}},
        "requires_platform_capabilities": {"type": "array", "items": {"type": "string"}},
        "interaction_profile": {"type": ["string", "null"]},
        "interaction_entries": {"type": "array", "items": interaction_entry},
        "event_subscriptions": {"type": "array", "items": event_subscription},
        "capabilities": {"type": ["object", "null"]},
        "config_schema": {"type": ["object", "null"]},
        "config_actions": {"type": "array", "items": config_action},
        "agent_tools": {"type": "array", "items": {"type": "object"}},
        "middleware": {"type": "array", "items": middleware_item},
        "render_profile": {"type": ["object", "null"]},
        "reads": {"type": "array", "items": {"type": "string", "pattern": r"^[a-z][a-z0-9_.]*@\d+$"}},
        "provides": {"type": "array", "items": {"type": "string"}},
        "payment_sources": {"type": "array", "items": payment_source},
        "ui": {"anyOf": [ui, {"type": "null"}]},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://telepilot.local/schemas/plugin-contract.schema.json",
        "title": f"TelePilot plugin.json（稳定契约 {PLUGIN_CONTRACT_VERSION}）",
        "description": (
            "由 scripts/export-plugin-contract.py 从运行时常量生成，不要手改。比 plugin.schema.json 多描述一层"
            "嵌套字段；所有对象允许额外字段（向前兼容）。"
        ),
        "type": "object",
        "additionalProperties": True,
        "anyOf": [{"required": ["name"]}, {"required": ["key"]}],
        "properties": properties,
    }


def build_contract() -> dict[str, Any]:
    """生成当前宿主的插件契约快照（确定性，可直接写入 JSON 比较）。"""

    facades = {label: _public_methods(cls) for label, cls in _facade_classes().items()}
    return {
        "$comment": (
            "TelePilot 插件稳定契约快照。由 scripts/export-plugin-contract.py 从运行时内省生成，"
            "不要手改；破坏性检查以 plugin-api-contract-1.0.frozen.json 为基线（docs/PLUGIN-CONTRACT.md）。"
        ),
        "contract_version": PLUGIN_CONTRACT_VERSION,
        "baseline_plugin_api": CONTRACT_BASELINE_PLUGIN_API,
        "plugin_api_version": PLUGIN_API_VERSION,
        "hooks": _hooks(),
        "context": _context(),
        "facades": facades,
        "capabilities": _capability_snapshot(),
        "manifest": _manifest(),
        "permissions": _permissions(),
        "ui": _ui(),
        "bridge": _bridge(),
        "events": _events(),
        "actions": _actions(),
        "extension_points": _extension_points(),
        "host_read_resources": _host_read(),
        "deprecations": _deprecations(),
    }


def dumps(contract: Mapping[str, Any]) -> str:
    return json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


# ─────────────────────────────────────────────────────
# 破坏性改动检查：基线 ⊆ 当前
# ─────────────────────────────────────────────────────
# 这些键的标量值允许变化（说明性字段，或只增不减的计数）；其余标量变化都算破坏。
MUTABLE_SCALAR_KEYS: frozenset[str] = frozenset(
    {
        "$comment",
        "summary",
        "plugin_api_version",  # 当前 Plugin API 版本会继续递增（MAJOR 变化另行检查）
        "widgets_contract_version",  # 控件契约只增不减（数值单调检查见下）
        "deprecated_in",  # 允许新增弃用公告；删除由 removed_in / 条目消失体现
        "returns",
        "timeout_s",
    }
)
# 宿主可以在“基线为 null”时补上值，但不能把已有值改掉。
_NAMED_LIST_KEYS = ("name", "id")


def _key_of(item: Mapping[str, Any]) -> str | None:
    for key in _NAMED_LIST_KEYS:
        if isinstance(item.get(key), str):
            return item[key]
    return None


def _compare_params(path: str, base: list[Any], cur: list[Any], problems: list[str]) -> None:
    cur_index = {p["name"]: (i, p) for i, p in enumerate(cur) if isinstance(p, Mapping)}
    base_names = [p["name"] for p in base if isinstance(p, Mapping)]
    for pos, p in enumerate(base):
        hit = cur_index.get(p["name"])
        if hit is None:
            if any(c.get("kind") == "VAR_KEYWORD" for c in cur) and p.get("kind") == "KEYWORD_ONLY":
                continue
            problems.append(f"{path}: 参数 {p['name']} 被删除")
            continue
        i, c = hit
        if c.get("kind") != p.get("kind"):
            problems.append(f"{path}: 参数 {p['name']} 类型 {p.get('kind')} → {c.get('kind')}")
        if p.get("kind") in {"POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD"} and i != pos:
            problems.append(f"{path}: 位置参数 {p['name']} 顺序 {pos} → {i}")
        if p.get("has_default") and not c.get("has_default"):
            problems.append(f"{path}: 参数 {p['name']} 由可选变为必填")
    for c in cur:
        if c["name"] in base_names or c.get("kind") in {"VAR_POSITIONAL", "VAR_KEYWORD"}:
            continue
        if not c.get("has_default"):
            problems.append(f"{path}: 新增必填参数 {c['name']}（新增参数必须有默认值）")


def breaking_changes(base: Any, cur: Any, path: str = "$") -> list[str]:
    """返回从 ``base`` 到 ``cur`` 的破坏性差异（空列表 = 兼容）。"""

    problems: list[str] = []
    _diff(base, cur, path, problems, key="")
    return problems


def _diff(base: Any, cur: Any, path: str, problems: list[str], key: str) -> None:
    if key == "params" and isinstance(base, list) and isinstance(cur, list):
        _compare_params(path, base, cur, problems)
        return
    if isinstance(base, Mapping):
        if not isinstance(cur, Mapping):
            problems.append(f"{path}: 结构从对象变为 {type(cur).__name__}")
            return
        for k, v in base.items():
            if k not in cur:
                problems.append(f"{path}.{k}: 被删除")
                continue
            _diff(v, cur[k], f"{path}.{k}", problems, key=k)
        return
    if isinstance(base, list):
        if not isinstance(cur, list):
            problems.append(f"{path}: 结构从数组变为 {type(cur).__name__}")
            return
        if path.startswith("$.manifest.fields."):
            missing = sorted(set(map(str, base)) - set(map(str, cur)))
            if missing and "any" not in cur:
                problems.append(f"{path}: 允许的类型被收窄，缺少 {missing}")
            return
        if all(isinstance(item, Mapping) and _key_of(item) for item in base):
            cur_map = {_key_of(item): item for item in cur if isinstance(item, Mapping)}
            for item in base:
                name = _key_of(item)
                if name not in cur_map:
                    problems.append(f"{path}[{name}]: 被删除")
                    continue
                _diff(item, cur_map[name], f"{path}[{name}]", problems, key="")
            return
        if all(not isinstance(item, (Mapping, list)) for item in base):
            missing = [item for item in base if item not in cur]
            if missing:
                problems.append(f"{path}: 缺少 {missing}")
            return
        for i, item in enumerate(base):  # pragma: no cover - 契约里没有这种结构
            if i >= len(cur):
                problems.append(f"{path}[{i}]: 被删除")
            else:
                _diff(item, cur[i], f"{path}[{i}]", problems, key="")
        return
    # 标量
    if base == cur:
        return
    if key == "plugin_api_version":
        if str(base).split(".")[0] != str(cur).split(".")[0]:
            problems.append(f"{path}: Plugin API MAJOR 变化 {base} → {cur}（契约 1.x 期间不允许）")
        return
    if key == "widgets_contract_version":
        if isinstance(base, int) and isinstance(cur, int) and cur < base:
            problems.append(f"{path}: 控件契约版本回退 {base} → {cur}")
        return
    if key in MUTABLE_SCALAR_KEYS:
        return
    if base is None and key in {"removed_in"}:
        problems.append(f"{path}: 能力被标记移除（契约 1.x 期间不允许）")
        return
    problems.append(f"{path}: {base!r} → {cur!r}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_contract_problems(current: Mapping[str, Any] | None = None) -> Iterable[str]:
    """当前宿主相对冻结基线的破坏性差异。"""

    current = current if current is not None else build_contract()
    yield from breaking_changes(load_json(FROZEN_BASELINE_PATH), current)


__all__ = [
    "CONTRACT_BASELINE_PLUGIN_API",
    "CONTRACT_PATH",
    "CONTRACT_SINCE_HOST",
    "FROZEN_BASELINE_PATH",
    "PLUGIN_CONTRACT_VERSION",
    "breaking_changes",
    "build_contract",
    "build_manifest_schema",
    "dumps",
    "iter_contract_problems",
]
