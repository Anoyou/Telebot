"""Plugin API 1.0 冻结范围：哪些接口进「保修单」（稳定），哪些只是「实验」。

第 22 轮项目负责人按 docs/PLUGIN-API-1.0-PLAN.md 第 2.2 节的 7 个问题拍板（1B 2B 3B 4B 5A 6B 7A）：

1. 标准动作字段（B）：6 种常用动作的常用字段稳定；通道覆盖、Rich Message 等高级字段实验。
2. Event Bus 信封（B）：高频标准栏目稳定；旧平铺字段（兼容层 C07 等）照留但不进保修单；其余实验。
3. ``ctx.client``（B）：方法名与权限的对应关系稳定，返回值以 Telethon 为准、不承诺；整体定级「高级 / 兼容」。
4. ``manifest.py`` 的 ``Manifest(...)``（B）：以 plugin.json 为准；与 plugin.json 对应的参数 + 官方插件在用参数稳定，其余实验。
5. AI 错误类型（A）：三个名字和「都属于 AIUnavailableError」的继承关系稳定（提示文字不冻结）。
6. 远程插件（B）：插件包文件结构与 Ed25519 签名格式稳定；入站 Webhook 信封实验。
7. 插件市场索引（A）：``registry.json`` 目录版本 1（``schema_version: 1``）的字段定死。

本模块只**登记**范围，不改变任何运行时行为：契约快照（:mod:`.contract`）把这里的稳定部分写进
``stability`` 段，冻结在 ``schemas/plugin-api-contract-1.0-scope.frozen.json``，CI 检查「冻结范围 ⊆ 当前」；
实验部分只写进当前快照供查阅，可以随时调整（会提前公告）。守护测试 ``test_plugin_api_stability.py``
确认每个稳定条目宿主今天确实还在提供（字段仍被读取 / 产出、方法仍在白名单、类仍在继承链上）。

删除或改名稳定条目只能放在契约 2.0（见 docs/PLUGIN-CONTRACT.md 第 3 节）。
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any

STABLE = "stable"
EXPERIMENTAL = "experimental"
# ctx.client：能用、方法清单不缩，但返回值不承诺；新插件请用标准动作 / ctx.messages。
ADVANCED_COMPAT = "advanced_compat"

# ─────────────────────────────────────────────────────
# 1. 标准动作字段
# ─────────────────────────────────────────────────────
# 动作类型名（20 种）早已在契约快照 actions 段冻结；这里再往下冻结 6 种常用动作的常用字段。
# ``type`` 对所有动作都是必填且稳定，不重复列出。
ACTION_FIELDS_STABLE: dict[str, tuple[str, ...]] = {
    "send_message": ("text", "chat_id", "reply_to_message_id", "parse_mode", "reply_markup", "save_message_id_key"),
    "edit_message": ("message_id", "chat_id", "text", "parse_mode", "reply_markup"),
    "delete_message": ("message_id", "chat_id"),
    "end_session": (),
    "answer_callback": ("callback_query_id", "text", "show_alert"),
    # 发奖：只冻结字段名与含义；结算仍由平台执行（“金额”是群内游戏币），不改任何结算行为。
    "payout": ("amount", "chat_id", "text", "reply_to_message_id", "parse_mode", "payout_key"),
}
# 已知的高级字段：照样能用，但可能调整（会提前公告）。未列出的字段同样不在保修范围内。
ACTION_FIELDS_EXPERIMENTAL: tuple[str, ...] = (
    "bot_api_extra",
    "channel_selector",
    "edit_message_id",
    "inline_message_id",
    "message_id_key",
    "pin",
    "render_profile",
    "replace_saved_message_id_key",
    "reply_anchor_missing_text",
    "reply_to_display_name",
    "reply_to_search_limit",
    "reply_to_user_id",
    "reply_to_username",
    "rich_message",
    "send_on_edit_failure",
    "send_via",
    "send_via_options",
    "suppress_reply_anchor_missing_notice",
)
# 兼容层照常生效、但不进保修单的写法（编号见 PLUGIN-API-1.0-PLAN.md 第 3 节）。
ACTION_FIELDS_COMPAT: dict[str, str] = {
    "compact_tables": "C14",
}

# ─────────────────────────────────────────────────────
# 2. Event Bus 信封（on_event / on_interaction 的 payload）
# ─────────────────────────────────────────────────────
# 顶层栏目 → 栏目里稳定的子字段。栏目在某类事件里可能为 null / 空对象（例如非转账事件没有 payment）。
EVENT_PAYLOAD_STABLE: dict[str, tuple[str, ...]] = {
    "source": ("type", "channel", "account_id", "update_id", "callback_query_id", "callback_data"),
    "message": ("text", "message_id", "chat_id", "reply_to_message_id"),
    "chat": ("id", "type"),
    "sender": ("user_id", "display_name", "username"),
    "actor": ("user_id", "display_name", "username"),
    "source_actor": ("user_id", "display_name"),
    "player": ("user_id", "display_name"),
    "payment": ("status", "amount", "payer_user_id", "payer_display_name", "receiver_display_name"),
    "reply_to": ("message_id",),
    "trigger": ("type", "rule_id", "module_key", "entry_key"),
    "session": ("key", "scope", "ttl_seconds", "active", "data"),
}
# 照样下发、但标「实验」的栏目（字段可能调整）。未列出的栏目同样不在保修范围内。
EVENT_PAYLOAD_EXPERIMENTAL: tuple[str, ...] = (
    "chosen_inline_result",
    "event_type",
    "inline_query",
    "module_config",
    "native_raw",
    "native_raw_meta",
    "raw",
    "webhook",
)
# 兼容层照留、不进保修单的旧平铺字段。
EVENT_PAYLOAD_COMPAT: dict[str, str] = {
    "owner_user_ids": "C07",
    "admin_user_ids": "C07",
}

# ─────────────────────────────────────────────────────
# 3. ctx.client（沙箱包装的 Telethon 客户端）
# ─────────────────────────────────────────────────────
# 权限 → 可调用方法。冻结「方法名 + 权限对应」，返回值是 Telethon 对象，以 Telethon 为准、不承诺。
CLIENT_PERMISSION_METHODS_STABLE: dict[str, tuple[str, ...]] = {
    "send_message": ("send_message", "respond", "reply"),
    "edit_message": ("edit", "edit_message"),
    "read_chat": ("get_messages", "get_chat", "iter_messages"),
    "resolve_entity": ("get_entity",),
    "send_file": ("send_file",),
    "forward_message": ("forward_messages",),
    "join_chat": ("join_chat",),
    "delete_message": ("delete_messages", "unpin_message"),
    "moderate_chat": ("ban_user", "kick_user", "mute_user", "unban_user"),
}
# 不需要声明权限就能调用的方法里，只承诺「查自己是谁」。
CLIENT_ALWAYS_ALLOWED_STABLE: tuple[str, ...] = ("get_me",)

# ─────────────────────────────────────────────────────
# 4. manifest.py 的 Manifest(...) 构造参数
# ─────────────────────────────────────────────────────
# 以 plugin.json 为准。稳定 = 与 plugin.json 字段对应的参数 + 官方插件库在用的参数。
# 参数一律按关键字传；只有 key、display_name 承诺位置（前两个位置参数）。
MANIFEST_PARAMS_STABLE: tuple[str, ...] = (
    "key",
    "display_name",
    "version",
    "min_telepilot_version",
    "author",
    "description",
    "usage",
    "permissions",
    "category",
    "requires_platform_capabilities",
    "config_schema",
    "config_actions",
    "interaction_entries",
    "interaction_profile",
    "event_subscriptions",
    "capabilities",
    "allowed_hosts",
    "agent_tools",
    # 下面几项推荐只写在 plugin.json（旧宿主的 Manifest 不接受，写进 manifest.py 会在旧宿主导入失败）；
    # 宿主加载时从 plugin.json 补齐，参数名同样稳定。
    "plugin_api",
    "reads",
    "middleware",
    "render_profile",
    "ui",
    "provides",
)
MANIFEST_POSITIONAL_STABLE: tuple[str, ...] = ("key", "display_name")
# 兼容层照留、不进保修单的参数。
MANIFEST_PARAMS_COMPAT: dict[str, str] = {
    "min_telebot_version": "C03",
}
# manifest.py 与 plugin.json 的一致性检查（插件库 check_host_compat.py 与主仓库示例测试）：
# Manifest 参数 → plugin.json 字段。不一致即报错；说明性文本以 plugin.json 为准，只给警告。
MANIFEST_PARITY_FIELDS: dict[str, str] = {
    "key": "name",
    "display_name": "display_name",
    "version": "version",
    "min_telepilot_version": "min_telepilot_version",
    "permissions": "permissions",
    "category": "category",
    "requires_platform_capabilities": "requires_platform_capabilities",
    "config_schema": "config_schema",
    "config_actions": "config_actions",
    "interaction_entries": "interaction_entries",
    "interaction_profile": "interaction_profile",
    "event_subscriptions": "event_subscriptions",
    "capabilities": "capabilities",
    "allowed_hosts": "allowed_hosts",
    "agent_tools": "agent_tools",
}
MANIFEST_PARITY_WARN_FIELDS: dict[str, str] = {
    "author": "author",
    "description": "description",
    "usage": "usage",
}

# ─────────────────────────────────────────────────────
# 5. AI 错误类型
# ─────────────────────────────────────────────────────
AI_ERRORS_MODULE = "app.worker.plugins.ai_facade"
AI_ERRORS_STABLE: dict[str, str] = {
    # 类名 → 必须继承的类（isinstance 判断不变）；错误提示文字不冻结。
    "AIUnavailableError": "AIUnavailableError",
    "AICapabilityError": "AIUnavailableError",
    "AIResponseTruncated": "AIUnavailableError",
}

# ─────────────────────────────────────────────────────
# 6. 远程插件：插件包与签名（稳定）；入站 Webhook 信封（实验）
# ─────────────────────────────────────────────────────
PACKAGE_REQUIRED_FILES: tuple[str, ...] = ("__init__.py", "manifest.py", "plugin.json", "plugin.py")
PACKAGE_SIGNATURE: dict[str, str] = {
    "algorithm": "Ed25519",
    "form": "detached",
    "signed_payload": "zip_bytes",
    "public_key_format": "PEM",
}
WEBHOOK_ENVELOPE_EXPERIMENTAL: tuple[str, ...] = ("webhook", "hook_key", "body", "headers")

# ─────────────────────────────────────────────────────
# 7. 插件市场索引 registry.json（目录版本 1 定死）
# ─────────────────────────────────────────────────────
REGISTRY_SCHEMA_VERSION = 1
REGISTRY_V1_TOP_FIELDS: tuple[str, ...] = ("schema_version", "name", "trust", "repository", "homepage", "plugins")
REGISTRY_V1_ENTRY_FIELDS: tuple[str, ...] = (
    "key",
    "name",
    "kind",
    "icon",
    "summary",
    "version",
    "author",
    "min_telepilot_version",
    "plugin_api",
    "permissions",
    "reads",
    "tags",
    "path",
    "channels",
    "repository",
    "trust",
)
# 目录版本 1.1 追加字段（0.117.0，插件市场详情页与稳定 / 测试通道）：全部可选，只增不减。旧宿主忽略这些字段；
# 原 16 个字段（REGISTRY_V1_ENTRY_FIELDS）与 summary 的字符串类型不变。插件库 build_registry.py 有同一份清单。
REGISTRY_V1_ADDITIVE_ENTRY_FIELDS: tuple[str, ...] = (
    "summary_i18n",
    "description",
    "usage",
    "commands",
    "release_notes",
    "released_at",
    "homepage",
    "license",
    "icon_url",
    "screenshots",
    "channel_versions",
)


# ─────────────────────────────────────────────────────
# manifest.py ↔ plugin.json 一致性
# ─────────────────────────────────────────────────────
_SORTED_LIST_FIELDS = frozenset({"permissions", "allowed_hosts", "requires_platform_capabilities"})


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _parity_value(attr: str, value: Any, *, with_property_order: Any = None) -> Any:
    if attr in _SORTED_LIST_FIELDS and isinstance(value, list):
        return sorted(str(item) for item in value)
    if attr == "config_schema" and isinstance(value, dict) and with_property_order is not None:
        return with_property_order(value)
    if attr in {"min_telepilot_version", "version"} and value is not None:
        return str(value).strip()
    return value


def manifest_parity_problems(raw: dict[str, Any], manifest: Any) -> tuple[list[str], list[str]]:
    """比较 plugin.json（``raw``）与 ``manifest.py`` 的 ``MANIFEST``，返回 ``(错误, 警告)``。

    以 plugin.json 为准：两边都写了且不一致才算错误；一边没写（空 / 缺省）不算矛盾——宿主加载时会从
    plugin.json 补齐 plugin_api / reads / ui 等字段，manifest.py 也可以只写必需项。``author`` /
    ``description`` / ``usage`` 这类说明文字不一致只给警告。插件库 ``scripts/check_host_compat.py`` 与主仓库示例测试共用。
    """

    from .manifest import with_property_order

    errors: list[str] = []
    warnings: list[str] = []

    def compare(attr: str, json_key: str, sink: list[str]) -> None:
        if attr == "min_telepilot_version":
            left = raw.get("min_telepilot_version") or raw.get("min_telebot_version")
            right = getattr(manifest, "min_telepilot_version", None) or getattr(manifest, "min_telebot_version", None)
        else:
            left = raw.get(json_key)
            right = getattr(manifest, attr, None)
        if _empty(left) or _empty(right):
            return
        left = _parity_value(attr, left, with_property_order=with_property_order)
        right = _parity_value(attr, right, with_property_order=with_property_order)
        if left != right:
            label = attr if attr == json_key else f"{json_key}（manifest.py 的 {attr}）"
            sink.append(f"plugin.json 与 manifest.py 的 {label} 不一致（以 plugin.json 为准）")

    for attr, json_key in MANIFEST_PARITY_FIELDS.items():
        compare(attr, json_key, errors)
    for attr, json_key in MANIFEST_PARITY_WARN_FIELDS.items():
        compare(attr, json_key, warnings)
    return errors, warnings


# ─────────────────────────────────────────────────────
# 契约快照段落
# ─────────────────────────────────────────────────────
def _manifest_params() -> dict[str, Any]:
    from .manifest import Manifest

    params = list(inspect.signature(Manifest).parameters.values())
    index = {p.name: i for i, p in enumerate(params)}
    by_name = {p.name: p for p in params}
    stable: dict[str, Any] = {}
    for name in MANIFEST_PARAMS_STABLE:
        p = by_name.get(name)
        if p is None:  # 守护测试会指出；快照里照实记录缺失
            stable[name] = {"present": False}
            continue
        item: dict[str, Any] = {"present": True, "has_default": p.default is not inspect.Parameter.empty}
        if name in MANIFEST_POSITIONAL_STABLE:
            item["position"] = index[name]
        stable[name] = item
    known = {f.name for f in dataclasses.fields(Manifest)}
    experimental = sorted(known - set(MANIFEST_PARAMS_STABLE) - set(MANIFEST_PARAMS_COMPAT))
    return {
        "stable": stable,
        "experimental": experimental,
        "compat": dict(MANIFEST_PARAMS_COMPAT),
        "parity_fields": dict(MANIFEST_PARITY_FIELDS),
        "parity_warn_fields": dict(MANIFEST_PARITY_WARN_FIELDS),
    }


def _client_methods() -> dict[str, Any]:
    # 从沙箱白名单内省：方法被移出白名单 / 换了权限，快照就会变，冻结范围检查随即失败。
    from . import sandbox

    stable = {perm: sorted(sandbox.ALLOWED_API.get(perm, ())) for perm in CLIENT_PERMISSION_METHODS_STABLE}
    always = [name for name in CLIENT_ALWAYS_ALLOWED_STABLE if name in sandbox._ALWAYS_ALLOWED]
    return {"stable": stable, "always_allowed": always}


def _package_required_files() -> list[str]:
    from ...services import plugin_install_service

    return sorted(set(plugin_install_service.REQUIRED_FILES) & set(PACKAGE_REQUIRED_FILES))


def _registry() -> dict[str, Any]:
    from ...services import plugin_market_service as market

    return {
        "schema_version": market.REGISTRY_SCHEMA_VERSION,
        "top_fields": list(REGISTRY_V1_TOP_FIELDS),
        "entry_fields": list(REGISTRY_V1_ENTRY_FIELDS),
        "additive_entry_fields": list(REGISTRY_V1_ADDITIVE_ENTRY_FIELDS),
        "kinds": list(market.KINDS),
        "trust_levels": list(market.TRUST_LEVELS),
    }


def _ai_errors() -> dict[str, Any]:
    import importlib

    module = importlib.import_module(AI_ERRORS_MODULE)
    out: dict[str, Any] = {}
    for name in AI_ERRORS_STABLE:
        cls = getattr(module, name, None)
        out[name] = {"ancestors": [base.__name__ for base in cls.__mro__ if base is not object] if isinstance(cls, type) else []}
    return out


def build_stability() -> dict[str, Any]:
    """契约快照的 ``stability`` 段：稳定部分进冻结范围，实验部分只做标注。"""

    return {
        "decided": "第 22 轮（2026-11-01）项目负责人拍板：1B 2B 3B 4B 5A 6B 7A",
        "action_fields": {
            "level": STABLE,
            "stable": {k: list(v) for k, v in ACTION_FIELDS_STABLE.items()},
            "experimental": list(ACTION_FIELDS_EXPERIMENTAL),
            "compat": dict(ACTION_FIELDS_COMPAT),
        },
        "event_payload": {
            "level": STABLE,
            "stable": {k: list(v) for k, v in EVENT_PAYLOAD_STABLE.items()},
            "experimental": list(EVENT_PAYLOAD_EXPERIMENTAL),
            "compat": dict(EVENT_PAYLOAD_COMPAT),
        },
        "client_methods": {"level": ADVANCED_COMPAT, "returns": "telethon", **_client_methods()},
        "manifest_py": {"level": STABLE, **_manifest_params()},
        "ai_errors": {"level": STABLE, "module": AI_ERRORS_MODULE, "stable": _ai_errors()},
        "remote_package": {
            "level": STABLE,
            "required_files": _package_required_files(),
            "signature": dict(PACKAGE_SIGNATURE),
        },
        "webhook_envelope": {"level": EXPERIMENTAL, "experimental": list(WEBHOOK_ENVELOPE_EXPERIMENTAL)},
        "registry": {"level": STABLE, **_registry()},
    }


# 冻结范围文件只收稳定部分：这些键（实验 / 兼容标注、说明文字）不进冻结范围，可以随时调整。
_NOT_FROZEN_KEYS = frozenset({"experimental", "compat", "decided", "parity_warn_fields"})


def stable_scope(stability: dict[str, Any]) -> dict[str, Any]:
    """从 ``stability`` 段里取出稳定部分（写入 / 对比 plugin-api-contract-1.0-scope.frozen.json）。"""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _NOT_FROZEN_KEYS}
        return node

    scope = strip(stability)
    scope.pop("webhook_envelope", None)  # 整块实验
    return scope


__all__ = [
    "ACTION_FIELDS_EXPERIMENTAL",
    "ACTION_FIELDS_STABLE",
    "ADVANCED_COMPAT",
    "AI_ERRORS_STABLE",
    "CLIENT_ALWAYS_ALLOWED_STABLE",
    "CLIENT_PERMISSION_METHODS_STABLE",
    "EVENT_PAYLOAD_STABLE",
    "EXPERIMENTAL",
    "MANIFEST_PARAMS_STABLE",
    "MANIFEST_PARITY_FIELDS",
    "MANIFEST_PARITY_WARN_FIELDS",
    "PACKAGE_REQUIRED_FILES",
    "PACKAGE_SIGNATURE",
    "REGISTRY_SCHEMA_VERSION",
    "REGISTRY_V1_ADDITIVE_ENTRY_FIELDS",
    "REGISTRY_V1_ENTRY_FIELDS",
    "REGISTRY_V1_TOP_FIELDS",
    "STABLE",
    "build_stability",
    "manifest_parity_problems",
    "stable_scope",
]
