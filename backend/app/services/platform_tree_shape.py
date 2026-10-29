"""运行树：按插件真实声明判断它「怎么接到平台上」。

只做静态读取（``plugin.json`` + ``manifest.py`` 字面量 + 源码 AST），
不 import、不执行第三方插件代码。

- ``channel``：消息从哪条路进来，三选一
  - ``passthrough`` 直通：声明 ``capabilities.telegram_direct_passthrough.enabled``，最先拿到原始消息；
  - ``interaction`` 交互 Bot：声明 ``interaction_entries`` / ``interaction_profile``，或依赖交互模块；
  - ``main`` 主通道：其余插件，靠账号自己的消息链路工作（命令、事件订阅、定时任务、消息监听……）。
- ``entry_kinds``：插件实际声明 / 实现了哪些入口，供界面写清「它靠什么工作」。

旧字段 ``attachment``（直通 / 命令 / 交互）保持原判定，其中「命令」= 这里的 ``main``。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .plugin_capability_requirements import PluginCapabilityRequirement

CHANNEL_LABELS: dict[str, str] = {
    "passthrough": "直通",
    "main": "主通道",
    "interaction": "交互 Bot",
}
LEGACY_ATTACHMENT: dict[str, str] = {
    "passthrough": "直通",
    "main": "命令",
    "interaction": "交互",
}
ENTRY_KIND_LABELS: dict[str, str] = {
    "passthrough": "直通",
    "command": "命令",
    "event": "事件订阅",
    "message": "消息监听",
    "schedule": "定时任务",
    "webhook": "Webhook",
    "interaction": "交互入口",
    "middleware": "消息中间件",
    "payment_source": "转账通知解析",
    "agent_tool": "Agent 工具",
}
ENTRY_KIND_ORDER: tuple[str, ...] = tuple(ENTRY_KIND_LABELS)

_DECLARATION_KEYS = (
    "capabilities",
    "interaction_entries",
    "interaction_profile",
    "event_subscriptions",
    "middleware",
    "payment_sources",
    "agent_tools",
    "entry",
)
_SKIP_DIRS = {"__pycache__", "tests", "test", "fixtures", "node_modules", ".git", "static", "assets"}
_MAX_SCAN_FILES = 80
_MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class PluginShape:
    channel: str
    entry_kinds: tuple[str, ...]

    @property
    def channel_label(self) -> str:
        return CHANNEL_LABELS[self.channel]

    @property
    def attachment(self) -> str:
        return LEGACY_ATTACHMENT[self.channel]

    @property
    def entry_labels(self) -> list[str]:
        return [ENTRY_KIND_LABELS[kind] for kind in self.entry_kinds]


_CACHE: dict[str, tuple[tuple[Any, ...], PluginShape]] = {}


def _read_plugin_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _read_manifest_literals(path: Path) -> dict[str, Any]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return {}
    out: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in _DECLARATION_KEYS and keyword.arg not in out:
                try:
                    out[keyword.arg] = ast.literal_eval(keyword.value)
                except (ValueError, SyntaxError, TypeError):
                    continue
    return out


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    try:
        candidates = sorted(root.rglob("*.py"))
    except OSError:
        return files
    for item in candidates:
        try:
            rel = item.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) > 3 or any(part in _SKIP_DIRS for part in rel.parts[:-1]):
            continue
        files.append(item)
        if len(files) >= _MAX_SCAN_FILES:
            break
    return files


def _scan_code(files: list[Path]) -> tuple[set[str], bool]:
    hooks: set[str] = set()
    uses_scheduler = False
    for item in files:
        try:
            if item.stat().st_size > _MAX_FILE_BYTES:
                continue
            tree = ast.parse(item.read_text(encoding="utf-8"), filename=str(item))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("on_"):
                hooks.add(node.name)
            elif isinstance(node, ast.Attribute) and node.attr == "scheduler":
                uses_scheduler = True
    return hooks, uses_scheduler


def _signature(root: Path, files: list[Path]) -> tuple[Any, ...]:
    parts: list[Any] = []
    for item in [root / "plugin.json", root / "manifest.py", *files]:
        try:
            stat = item.stat()
        except OSError:
            parts.append((str(item), None))
            continue
        parts.append((str(item), stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _compute(root: Path, channel: str, files: list[Path]) -> PluginShape:
    declarations = _read_manifest_literals(root / "manifest.py") if (root / "manifest.py").is_file() else {}
    if (root / "plugin.json").is_file():
        declarations.update(
            {k: v for k, v in _read_plugin_json(root / "plugin.json").items() if k in _DECLARATION_KEYS}
        )
    hooks, uses_scheduler = _scan_code(files)

    capabilities = declarations.get("capabilities")
    passthrough = capabilities.get("telegram_direct_passthrough") if isinstance(capabilities, dict) else None
    is_passthrough = isinstance(passthrough, dict) and passthrough.get("enabled") is True
    has_interaction = bool(declarations.get("interaction_entries") or declarations.get("interaction_profile"))

    kinds: set[str] = set()
    if is_passthrough or channel == "passthrough":
        kinds.add("passthrough")
    events: set[str] = set()
    sources: set[str] = set()
    for item in _as_list(declarations.get("event_subscriptions")):
        if not isinstance(item, dict):
            continue
        events.update(str(e) for e in _as_list(item.get("events")) if isinstance(e, str))
        sources.update(str(s) for s in _as_list(item.get("source")) if isinstance(s, str))
    if "command" in events or "on_command" in hooks:
        kinds.add("command")
    if "webhook" in events or "webhook" in sources:
        kinds.add("webhook")
    if events - {"webhook", "command"}:
        kinds.add("event")
    if hooks & {"on_message", "on_message_edited"}:
        kinds.add("message")
    if uses_scheduler:
        kinds.add("schedule")
    if has_interaction or "on_interaction" in hooks:
        kinds.add("interaction")
    if _as_list(declarations.get("middleware")):
        kinds.add("middleware")
    if _as_list(declarations.get("payment_sources")):
        kinds.add("payment_source")
    if _as_list(declarations.get("agent_tools")):
        kinds.add("agent_tool")
    return PluginShape(
        channel=channel,
        entry_kinds=tuple(kind for kind in ENTRY_KIND_ORDER if kind in kinds),
    )


def channel_from_attachment(attachment: str) -> str:
    for channel, legacy in LEGACY_ATTACHMENT.items():
        if legacy == attachment:
            return channel
    return "main"


def plugin_shape(record: PluginCapabilityRequirement, attachment: str) -> PluginShape:
    """返回插件的接入通道与入口种类；按文件签名缓存，源码变化自动重算。

    ``channel`` 直接由旧字段 ``attachment`` 换算（直通→passthrough、交互→interaction、
    命令→main），保证新旧字段永远一致；``entry_kinds`` 才是本模块新增的细分。
    """

    channel = channel_from_attachment(attachment)
    root = record.path
    if record.source_missing or not root.is_dir():
        return PluginShape(channel=channel, entry_kinds=())
    files = _source_files(root)
    signature = (_signature(root, files), channel)
    cached = _CACHE.get(str(root))
    if cached is not None and cached[0] == signature:
        return cached[1]
    shape = _compute(root, channel, files)
    _CACHE[str(root)] = (signature, shape)
    return shape


def _reset_for_tests() -> None:
    _CACHE.clear()


__all__ = [
    "CHANNEL_LABELS",
    "ENTRY_KIND_LABELS",
    "PluginShape",
    "channel_from_attachment",
    "plugin_shape",
]
