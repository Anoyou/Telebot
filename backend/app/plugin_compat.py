"""历史插件兼容表：宿主里所有“按插件 key 写死”的行为都集中在这里（0.108.0 起）。

背景：插件平台化审计（2026-10-29）统计到 13 个非测试文件按插件 key 写死了分支（math10 兜底、
codex_image 模型改名、chatgpt_image 多 Token 配置、兜底入口表……）。这些都是插件迁出 Core
之前留下的兼容逻辑，**新插件一律走通用声明**（``interaction_entries[]`` 的 ``session_scope`` /
``participant_policy`` / 入口 schema、Event Bus、``ctx.host.read`` 等），不再往这里加条目。

规则：

- 业务代码只通过本模块的数据表与函数判断，不再直接写插件 key 字面量；
- 每一项写明“为什么还在”和计划移除的版本；插件一旦在 manifest 里声明了对应信息，
  声明优先，表里的兜底不再生效（见 ``account_bot_service._declared_module_*``）；
- 本模块只依赖标准库，主进程、worker 都可导入。
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────
# 插件 key（只在本模块出现字面量）
# ─────────────────────────────────────────────────────
AUTO_REPLY = "auto_reply"
AUTOREPEAT = "autorepeat"
CHATGPT_IMAGE = "chatgpt_image"
CODEX_IMAGE = "codex_image"
GAME24 = "game24"
MATH10 = "math10"
DICE_GRID_HUNT = "dice_grid_hunt"
GUESS_NUMBER = "guess_number"
PT_PROMOTE = "pt_promote"
BLACKJACK = "blackjack"

# 已迁出 Core 的非核心可选插件（feature_registry.NON_CORE_OPTIONAL_PLUGIN_KEYS 引用这一份）。
# 计划移除：Plugin API 2.0（届时旧镜像残留目录与旧 builtin 记录都已迁移完成）。
NON_CORE_OPTIONAL_PLUGIN_KEYS: frozenset[str] = frozenset({AUTO_REPLY, AUTOREPEAT, CHATGPT_IMAGE, CODEX_IMAGE, GAME24, MATH10})

# ─────────────────────────────────────────────────────
# 交互入口兜底（旧插件 manifest 没声明 session_scope / participant_policy / prize 时才用）
# 计划移除：Plugin API 2.0。新插件请在 interaction_entries[] 里声明。
# ─────────────────────────────────────────────────────
FALLBACK_CHAT_SESSION_MODULE_ENTRIES: frozenset[tuple[str, str]] = frozenset(
    {
        (DICE_GRID_HUNT, "start_dice_grid_hunt"),
        (DICE_GRID_HUNT, "answer_dice_grid_hunt"),
        (GAME24, "start_paid_game"),
        (MATH10, "start_math10"),
        (GUESS_NUMBER, "start_game"),
    }
)
FALLBACK_NO_PRIZE_MODULE_ENTRIES: frozenset[tuple[str, str]] = frozenset({(PT_PROMOTE, "promote_torrent")})
FALLBACK_SOLO_OWNER_MODULE_ENTRIES: frozenset[tuple[str, str]] = frozenset({(BLACKJACK, "start_blackjack")})


def fallback_entry_keys(module_key: str | None) -> list[str]:
    """兜底表里某模块的入口 key（旧插件没声明 interaction_entries 时推断单入口）。"""

    if not module_key:
        return []
    return sorted(
        entry_key
        for key, entry_key in (
            FALLBACK_CHAT_SESSION_MODULE_ENTRIES | FALLBACK_NO_PRIZE_MODULE_ENTRIES | FALLBACK_SOLO_OWNER_MODULE_ENTRIES
        )
        if key == module_key
    )


def fallback_entry_session_scope(module_key: str | None, entry_key: str | None) -> str | None:
    return "chat" if (module_key, entry_key) in FALLBACK_CHAT_SESSION_MODULE_ENTRIES else None


def fallback_entry_participant_policy(module_key: str | None, entry_key: str | None) -> str | None:
    return "solo_owner" if (module_key, entry_key) in FALLBACK_SOLO_OWNER_MODULE_ENTRIES else None


def fallback_entry_has_field(module_key: str | None, entry_key: str | None, field_name: str) -> bool | None:
    if field_name == "prize" and (module_key, entry_key) in FALLBACK_NO_PRIZE_MODULE_ENTRIES:
        return False
    return None


# ─────────────────────────────────────────────────────
# 交互规则旧动作 action="math10"（0.2x 时代的“算数题”规则，存量规则仍在用）
# 运行时会转换成 action="module" + module_key="math10"。计划移除：迁移脚本改写存量规则之后。
# ─────────────────────────────────────────────────────
LEGACY_RULE_ACTION_MATH10 = MATH10
LEGACY_RULE_MODULE_ACTIONS: frozenset[str] = frozenset({LEGACY_RULE_ACTION_MATH10, "module"})
RULE_ACTIONS: frozenset[str] = frozenset({"notice", LEGACY_RULE_ACTION_MATH10, "module"})
DEFAULT_MATH10_START_KEYWORDS: tuple[str, ...] = ("发十以内算数", "十以内算数", "开算数题")
LEGACY_RULE_ACTION_LABELS: dict[str, str] = {LEGACY_RULE_ACTION_MATH10: "算数题"}


def is_legacy_math10_action(action: Any) -> bool:
    return str(action or "").strip() == LEGACY_RULE_ACTION_MATH10


# 主进程有本地兜底实现的交互模块：worker 报“模块未加载或未启用”时先不写误导性 WARN。
MODULES_WITH_MAIN_PROCESS_FALLBACK: frozenset[str] = frozenset({MATH10})


def has_main_process_fallback(plugin_key: str | None) -> bool:
    return str(plugin_key or "") in MODULES_WITH_MAIN_PROCESS_FALLBACK


# ─────────────────────────────────────────────────────
# 插件配置的历史改写（旧值 → 新值），保存配置时执行。新插件请在插件内自行迁移配置。
# ─────────────────────────────────────────────────────
LEGACY_CONFIG_VALUE_RENAMES: dict[str, dict[str, dict[Any, Any]]] = {
    # gpt-5.4 下线后统一改到 gpt-5.5（0.9x）。
    CODEX_IMAGE: {"model": {"gpt-5.4": "gpt-5.5"}},
}


def normalize_legacy_feature_config(plugin_key: str | None, config: dict[str, Any]) -> dict[str, Any]:
    renames = LEGACY_CONFIG_VALUE_RENAMES.get(str(plugin_key or ""))
    if not renames:
        return config
    normalized = dict(config)
    for field_name, mapping in renames.items():
        value = normalized.get(field_name)
        try:
            if value in mapping:
                normalized[field_name] = mapping[value]
        except TypeError:
            continue
    return normalized


# 配置里用“多 Token 列表”保存密钥的旧版插件。0.110.0 起通用 schema 声明 ``x-secret-list``
# （services/plugin_secret_list.py）取代写死逻辑；这里只为没有声明的旧版本补一份等价声明。
MULTI_TOKEN_CONFIG_PLUGINS: frozenset[str] = frozenset({CHATGPT_IMAGE})
LEGACY_SECRET_LIST_FIELDS: dict[str, dict[str, dict[str, str]]] = {
    CHATGPT_IMAGE: {
        "tokens": {"secret_field": "token", "note_field": "note", "id_field": "token_id", "legacy_field": "token"},
    },
}


def uses_multi_token_config(plugin_key: str | None) -> bool:
    return str(plugin_key or "") in MULTI_TOKEN_CONFIG_PLUGINS


def legacy_secret_list_fields(plugin_key: str | None, schema: Any = None) -> dict[str, dict[str, str]]:
    """旧版插件（config_schema 没有 ``x-secret-list``）的等价密钥列表声明。"""

    specs = LEGACY_SECRET_LIST_FIELDS.get(str(plugin_key or ""))
    if not specs:
        return {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(properties, dict) and any(
        isinstance(field, dict) and field.get("x-secret-list") for field in properties.values()
    ):
        return {}
    return {key: dict(value) for key, value in specs.items()}


# ─────────────────────────────────────────────────────
# ,ai 指令图片后端：image_backend="codex_image" 桥接到同名插件（B8，计划改为 provides: image_backend）
# ─────────────────────────────────────────────────────
DEFAULT_IMAGE_BACKEND_PLUGIN = CODEX_IMAGE
IMAGE_BACKENDS: tuple[str, ...] = (DEFAULT_IMAGE_BACKEND_PLUGIN, "llm")

# 缺失插件时的安装提示（只影响日志文案）。
MISSING_PLUGIN_HINTS: dict[str, str] = {
    CODEX_IMAGE: "请先在“安装插件”页安装 Codex 图片生成，并确认 plugins/installed/codex_image 存在；",
}


def missing_plugin_hint(plugin_key: str) -> str | None:
    return MISSING_PLUGIN_HINTS.get(str(plugin_key or ""))

