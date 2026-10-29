"""消息管线中间件（0.109.0，Plugin API 1.2，能力 ``middleware.*``）。

插件在 ``plugin.json`` 里声明（唯一可信来源；``manifest.py`` 不用写）::

    "permissions": ["message_middleware"],          # sources 含 any_plugin 时必须
    "middleware": [
      {"stage": "outbound", "handler": "on_outbound", "order": 500,
       "match": {"action_types": ["send_message", "send_rich_message", "edit_message"],
                 "channels": ["interaction_bot", "userbot_reply"], "sources": ["self", "any_plugin"]},
       "timeout_ms": 250},
      {"stage": "inbound", "handler": "on_inbound", "match": {"events": ["message"]}},
      {"stage": "delivered", "handler": "on_delivered"}
    ]

插件类上实现同名方法，签名统一为 ``async def handler(self, ctx, value, mw)``：

========== ======================================== ===============================================
阶段        位置                                       返回
========== ======================================== ===============================================
inbound    事件信封生成之后、路由 / 插件入口之前（worker）    ``None`` 原样；``{"text": …, "annotations": …}``
                                                      补丁；``mw.drop(reason)`` 丢弃事件
outbound   插件返回动作、规范化之后、投递之前（worker）      ``None`` 原样；改过的动作 dict；
                                                      ``mw.reject(code, message)`` 拒绝该动作
delivered  投递结果确定之后，异步执行、不阻塞投递           忽略返回值
========== ======================================== ===============================================

执行规则：

- **只读视图 + 结构共享**：钩子拿到的是顶层浅拷贝，嵌套的 dict / list（``rich_message``、
  ``reply_markup``、``context`` …）是只读视图，不做深拷贝；要改嵌套值就整体替换
  （``action["reply_markup"] = mw.thaw(action["reply_markup"])`` 再改）。没被替换的值原样共享，
  大 ``rich_message`` 不会因为经过 N 个中间件被复制 N 次；
- **字段白名单**：outbound 只能改 :data:`OUTBOUND_MUTABLE_FIELDS`（正文、Rich、按钮、
  render_profile …），改了 ``type`` / ``chat_id`` / 通道 / settlement / 金额等任何别的字段，
  **整次修改回滚**并算一次失败；inbound 补丁只能有 ``text`` 与 ``annotations``（只写进
  ``annotations[<本插件 key>]``）；
- **fail-open**：异常、超时、返回畸形值都丢弃该钩子的修改、按原样继续，并计入统计与熔断
  （超时 ``reason_code=plugin_timeout``）；只有平台 ``core.*`` 中间件可以 fail-closed；
- **预算**：单钩子默认 250 ms（50–2000 可调），一条消息整条链 1 s，超出后剩余中间件跳过；
  流式中间态（``stream_preview``，需在 ``action_types`` 显式声明）每个钩子 50 ms；
- **链长**：每个账号每个阶段最多 :data:`MAX_CHAIN_LENGTH` 个插件中间件（按 order、插件 key 排序，
  多出的不生效并在加载日志里警告）；
- **顺序**：插件 ``order`` 限制在 1–899，``core.render_profile`` (900) / ``core.rich_compact`` (950)
  总在最后，插件新增的表格同样套平台样式；
- **统计**：每次调用记在 ``[账号 × 插件 × middleware.<stage> × 动作/事件类型]``，Web 插件详情
  「运行统计」可见；超时、失败与熔断和其它钩子同口径。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ...services import render_profile as core_render
from . import blocking_monitor, plugin_stats

log = logging.getLogger(__name__)

STAGE_INBOUND = core_render.STAGE_INBOUND
STAGE_OUTBOUND = core_render.STAGE_OUTBOUND
STAGE_DELIVERED = core_render.STAGE_DELIVERED
STAGES: tuple[str, ...] = (STAGE_INBOUND, STAGE_OUTBOUND, STAGE_DELIVERED)

DEFAULT_TIMEOUT_MS = 250
MIN_TIMEOUT_MS = 50
MAX_TIMEOUT_MS = 2000
STREAM_PREVIEW_TIMEOUT_MS = 50
CHAIN_BUDGET_MS = 1000
MAX_CHAIN_LENGTH = 8
ORDER_MIN = 1
ORDER_MAX = 899
DEFAULT_ORDER = 500
ANNOTATION_MAX_BYTES = 4096
MIDDLEWARE_PERMISSION = "message_middleware"

STREAM_PREVIEW_ACTION = "stream_preview"
OUTBOUND_DEFAULT_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "send_message",
        "send_rich_message",
        "edit_message",
        "send_media",
        "send_photo",
        "send_document",
        "send_file",
        "edit_caption",
        "answer_guest_query",
        "answer_inline_query",
        "answer_callback",
    }
)
OUTBOUND_ACTION_TYPES: frozenset[str] = OUTBOUND_DEFAULT_ACTION_TYPES | {STREAM_PREVIEW_ACTION}
OUTBOUND_MUTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "text",
        "caption",
        "parse_mode",
        "rich_message",
        "reply_markup",
        core_render.RENDER_PROFILE_KEY,
        "disable_web_page_preview",
        "link_preview_options",
        "disable_notification",
        "result",
        "results",
        "annotations",
    }
)
INBOUND_PATCH_FIELDS: frozenset[str] = frozenset({"text", "annotations"})
OUTBOUND_CHANNELS: frozenset[str] = frozenset({"interaction_bot", "userbot_reply"})
SOURCE_SELF = "self"
SOURCE_ANY_PLUGIN = "any_plugin"
SOURCE_SYSTEM = "system"
OUTBOUND_SOURCES: frozenset[str] = frozenset({SOURCE_SELF, SOURCE_ANY_PLUGIN, SOURCE_SYSTEM})
INBOUND_EVENT_SOURCES: frozenset[str] = frozenset({"userbot", "interaction_bot"})
REASON_FIELD_VIOLATION = "middleware_field_violation"
REASON_MALFORMED = "middleware_malformed_result"
REASON_REJECTED = "middleware_rejected"
REASON_DROPPED = "middleware_dropped"
_INTERNAL_PREFIX = "_tp"
_DELIVERED_MARK = "_tp_mw_delivered"


class MiddlewareDeclarationError(ValueError):
    """plugin.json ``middleware`` 写法不合法。"""


class MiddlewareReject(Exception):
    """outbound 中间件拒绝发送当前动作（``mw.reject``）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = str(code or "rejected")[:64]
        self.message = str(message or "")[:300]


class MiddlewareDrop(Exception):
    """inbound 中间件丢弃当前事件（``mw.drop``）。"""

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason or "dropped")
        self.reason = str(reason or "dropped")[:300]


class _Violation(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


# ─────────────────────────────────────────────────────
# 只读视图（结构共享）
# ─────────────────────────────────────────────────────
class FrozenDict(Mapping[str, Any]):
    """嵌套 dict 的只读视图：读到的子容器同样是只读视图，修改会抛 TypeError。

    ``copy.deepcopy(view)`` / ``copy.copy(view)`` / ``mw.thaw(view)`` 都返回可修改的独立深拷贝。
    """

    __slots__ = ("_target",)

    def __init__(self, target: dict[str, Any]) -> None:
        object.__setattr__(self, "_target", target)

    def __getitem__(self, key: str) -> Any:
        return freeze(self._target[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._target)

    def __len__(self) -> int:
        return len(self._target)

    def __contains__(self, key: object) -> bool:
        return key in self._target

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenDict):
            return self._target == other._target
        if isinstance(other, Mapping):
            return self._target == thaw(other)
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"FrozenDict({self._target!r})"

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("中间件拿到的是只读视图；请整体替换字段，或用 mw.thaw(...) 得到可修改副本")

    def __copy__(self) -> dict[str, Any]:
        return deepcopy(self._target)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        return deepcopy(self._target, memo)

    def unwrap_shared(self) -> dict[str, Any]:  # 平台内部：取回共享的原对象
        return self._target


class FrozenList(Sequence[Any]):
    """嵌套 list 的只读视图。"""

    __slots__ = ("_target",)

    def __init__(self, target: list[Any]) -> None:
        object.__setattr__(self, "_target", target)

    def __getitem__(self, index: Any) -> Any:  # type: ignore[override]
        if isinstance(index, slice):
            return [freeze(item) for item in self._target[index]]
        return freeze(self._target[index])

    def __len__(self) -> int:
        return len(self._target)

    def __iter__(self) -> Iterator[Any]:
        return (freeze(item) for item in self._target)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenList):
            return self._target == other._target
        if isinstance(other, (list, tuple)):
            return self._target == thaw(list(other))
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __add__(self, other: Any) -> list[Any]:
        return [*self, *other]

    def __repr__(self) -> str:
        return f"FrozenList({self._target!r})"

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("中间件拿到的是只读视图；请整体替换字段，或用 mw.thaw(...) 得到可修改副本")

    def __copy__(self) -> list[Any]:
        return deepcopy(self._target)

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        return deepcopy(self._target, memo)

    def unwrap_shared(self) -> list[Any]:
        return self._target


def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenDict(value)
    if isinstance(value, list):
        return FrozenList(value)
    return value


def thaw(value: Any) -> Any:
    """只读视图（或含只读视图的新容器）→ 可修改的独立深拷贝。"""

    if isinstance(value, (FrozenDict, FrozenList)):
        return deepcopy(value.unwrap_shared())
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def _unfreeze_shared(value: Any) -> Any:
    """钩子返回值 → 普通 JSON 结构：没被替换的只读视图取回共享原对象（结构共享，不复制）。"""

    if isinstance(value, (FrozenDict, FrozenList)):
        return value.unwrap_shared()
    if isinstance(value, dict):
        return {key: _unfreeze_shared(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {key: _unfreeze_shared(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_unfreeze_shared(item) for item in value]
    return value


def _view_of(value: dict[str, Any]) -> dict[str, Any]:
    """顶层浅拷贝 + 嵌套只读视图；平台内部键（``_tp*``）不给插件看。"""

    return {key: freeze(item) for key, item in value.items() if not str(key).startswith(_INTERNAL_PREFIX)}


# ─────────────────────────────────────────────────────
# 声明
# ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class MiddlewareSpec:
    plugin_key: str
    stage: str
    handler: str
    order: int = DEFAULT_ORDER
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    action_types: frozenset[str] = OUTBOUND_DEFAULT_ACTION_TYPES
    channels: frozenset[str] | None = None
    sources: frozenset[str] = frozenset({SOURCE_SELF})
    events: frozenset[str] | None = None
    event_sources: frozenset[str] | None = None
    index: int = 0

    @property
    def name(self) -> str:
        return f"{self.plugin_key}.{self.handler}"

    def matches_action(self, action_type: str, channels: Iterable[str], source_plugin_key: str | None) -> bool:
        if action_type not in self.action_types:
            return False
        if self.channels is not None and not (set(channels) & self.channels):
            return False
        if source_plugin_key is None:
            return SOURCE_SYSTEM in self.sources
        if source_plugin_key == self.plugin_key:
            return SOURCE_SELF in self.sources or SOURCE_ANY_PLUGIN in self.sources
        return SOURCE_ANY_PLUGIN in self.sources

    def matches_event(self, event_type: str | None, source: str | None) -> bool:
        if self.events is not None and (event_type or "") not in self.events:
            return False
        if self.event_sources is not None and source and source not in self.event_sources:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "stage": self.stage,
            "handler": self.handler,
            "order": self.order,
            "timeout_ms": self.timeout_ms,
        }
        if self.stage == STAGE_OUTBOUND:
            out["match"] = {
                "action_types": sorted(self.action_types),
                "channels": sorted(self.channels) if self.channels is not None else None,
                "sources": sorted(self.sources),
            }
        elif self.stage == STAGE_INBOUND:
            out["match"] = {
                "events": sorted(self.events) if self.events is not None else None,
                "sources": sorted(self.event_sources) if self.event_sources is not None else None,
            }
        else:
            out["match"] = {
                "action_types": sorted(self.action_types),
                "sources": sorted(self.sources),
            }
        return out


def _str_set(value: Any, *, field_name: str, allowed: frozenset[str] | None, strict: bool, errors: list[str]) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        errors.append(f"{field_name} 必须是字符串数组")
        return None
    items = {str(item or "").strip() for item in value if str(item or "").strip()}
    if allowed is not None:
        unknown = sorted(items - allowed)
        if unknown:
            errors.append(f"{field_name} 含不支持的值：{', '.join(unknown)}（允许：{', '.join(sorted(allowed))}）")
            items &= allowed
    return frozenset(items) if items else None


def parse_middleware(
    raw: Any,
    *,
    plugin_key: str,
    permissions: Iterable[str] | None = None,
    strict: bool = False,
) -> tuple[list[MiddlewareSpec], list[str]]:
    """解析 plugin.json ``middleware``；返回 (有效声明, 问题列表)。``strict=True`` 时有问题直接抛错。"""

    errors: list[str] = []
    specs: list[MiddlewareSpec] = []
    if raw in (None, []):
        return specs, errors
    if not isinstance(raw, list):
        errors.append("middleware 必须是数组")
        if strict:
            raise MiddlewareDeclarationError("；".join(errors))
        return specs, errors
    granted = {str(item or "").strip() for item in (permissions or [])}
    for index, item in enumerate(raw):
        where = f"middleware[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where} 必须是对象")
            continue
        local: list[str] = []
        stage = str(item.get("stage") or "").strip()
        if stage not in STAGES:
            errors.append(f"{where}.stage 只能是 {' / '.join(STAGES)}")
            continue
        handler = str(item.get("handler") or "").strip()
        if not handler.isidentifier() or handler.startswith("_"):
            errors.append(f"{where}.handler 必须是插件类上的公开方法名")
            continue
        if item.get("critical"):
            local.append(f"{where}.critical 只允许平台 core.* 中间件（第三方插件一律 fail-open）")
        try:
            order = int(item.get("order", DEFAULT_ORDER))
        except (TypeError, ValueError):
            order = DEFAULT_ORDER
            local.append(f"{where}.order 必须是整数")
        if not ORDER_MIN <= order <= ORDER_MAX:
            local.append(f"{where}.order 只能在 {ORDER_MIN}–{ORDER_MAX}（900+ 留给平台 core.*）")
            order = min(ORDER_MAX, max(ORDER_MIN, order))
        try:
            timeout_ms = int(item.get("timeout_ms", DEFAULT_TIMEOUT_MS))
        except (TypeError, ValueError):
            timeout_ms = DEFAULT_TIMEOUT_MS
            local.append(f"{where}.timeout_ms 必须是整数")
        if not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
            local.append(f"{where}.timeout_ms 只能在 {MIN_TIMEOUT_MS}–{MAX_TIMEOUT_MS}")
            timeout_ms = min(MAX_TIMEOUT_MS, max(MIN_TIMEOUT_MS, timeout_ms))
        match = item.get("match") if isinstance(item.get("match"), dict) else {}
        if item.get("match") is not None and not isinstance(item.get("match"), dict):
            local.append(f"{where}.match 必须是对象")
        action_types = OUTBOUND_DEFAULT_ACTION_TYPES
        channels = None
        sources = frozenset({SOURCE_SELF})
        events = None
        event_sources = None
        if stage in (STAGE_OUTBOUND, STAGE_DELIVERED):
            parsed_types = _str_set(
                match.get("action_types"),
                field_name=f"{where}.match.action_types",
                allowed=OUTBOUND_ACTION_TYPES if stage == STAGE_OUTBOUND else OUTBOUND_DEFAULT_ACTION_TYPES,
                strict=strict,
                errors=local,
            )
            if parsed_types:
                action_types = parsed_types
            channels = _str_set(match.get("channels"), field_name=f"{where}.match.channels", allowed=OUTBOUND_CHANNELS, strict=strict, errors=local)
            parsed_sources = _str_set(match.get("sources"), field_name=f"{where}.match.sources", allowed=OUTBOUND_SOURCES, strict=strict, errors=local)
            if parsed_sources:
                sources = parsed_sources
            if SOURCE_ANY_PLUGIN in sources and MIDDLEWARE_PERMISSION not in granted:
                local.append(f"{where}.match.sources 含 any_plugin 时需要在 permissions 里声明 {MIDDLEWARE_PERMISSION}")
                sources = (sources - {SOURCE_ANY_PLUGIN}) or frozenset({SOURCE_SELF})
        else:
            events = _str_set(match.get("events"), field_name=f"{where}.match.events", allowed=None, strict=strict, errors=local)
            if events is None:
                events = frozenset({"message"})
            event_sources = _str_set(match.get("sources"), field_name=f"{where}.match.sources", allowed=INBOUND_EVENT_SOURCES, strict=strict, errors=local)
            if MIDDLEWARE_PERMISSION not in granted:
                local.append(f"{where}：inbound 中间件会看到并可丢弃所有插件的事件，需要在 permissions 里声明 {MIDDLEWARE_PERMISSION}")
                errors.extend(local)
                continue
        errors.extend(local)
        specs.append(
            MiddlewareSpec(
                plugin_key=plugin_key,
                stage=stage,
                handler=handler,
                order=order,
                timeout_ms=timeout_ms,
                action_types=action_types,
                channels=channels,
                sources=sources,
                events=events,
                event_sources=event_sources,
                index=index,
            )
        )
    if strict and errors:
        raise MiddlewareDeclarationError("；".join(errors))
    return specs, errors


# ─────────────────────────────────────────────────────
# 每个账号的中间件链
# ─────────────────────────────────────────────────────
@dataclass
class _Link:
    spec: MiddlewareSpec
    inst: Any
    ctx: Any


@dataclass
class Chains:
    key: tuple[Any, ...]
    inbound: list[_Link] = field(default_factory=list)
    outbound: list[_Link] = field(default_factory=list)
    delivered: list[_Link] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    def for_stage(self, stage: str) -> list[_Link]:
        return getattr(self, stage)

    @property
    def empty(self) -> bool:
        return not (self.inbound or self.outbound or self.delivered)


_EMPTY = Chains(key=())


def manifest_of(inst: Any) -> Any:
    return getattr(type(inst), "_manifest", None)


def specs_for_instance(plugin_key: str, inst: Any) -> list[MiddlewareSpec]:
    manifest = manifest_of(inst)
    cached = getattr(type(inst), "_tp_middleware_specs", None)
    if isinstance(cached, tuple) and cached and cached[0] == id(manifest):
        return list(cached[1])
    raw = getattr(manifest, "middleware", None) if manifest is not None else None
    specs, problems = parse_middleware(raw, plugin_key=plugin_key, permissions=getattr(manifest, "permissions", None))
    for problem in problems:
        log.warning("插件 %s 的 middleware 声明有问题（已按规则修正或忽略）：%s", plugin_key, problem)
    try:
        type(inst)._tp_middleware_specs = (id(manifest), tuple(specs))
    except Exception:  # noqa: BLE001
        pass
    return specs


def chains_for(state: Any) -> Chains:
    """按当前已加载插件构建（并缓存）三阶段中间件链；每阶段最多 :data:`MAX_CHAIN_LENGTH` 个。"""

    instances: dict[str, Any] = getattr(state, "instances", None) or {}
    contexts: dict[str, Any] = getattr(state, "contexts", None) or {}
    if not instances:
        return _EMPTY
    key = (getattr(state, "generation", 0), tuple((name, id(inst), id(contexts.get(name))) for name, inst in instances.items()))
    cached = getattr(state, "_tp_middleware_chains", None)
    if isinstance(cached, Chains) and cached.key == key:
        return cached
    chains = Chains(key=key)
    for plugin_key, inst in instances.items():
        ctx = contexts.get(plugin_key)
        if ctx is None:
            continue
        for spec in specs_for_instance(plugin_key, inst):
            if not callable(getattr(inst, spec.handler, None)):
                log.warning("插件 %s 声明的中间件 handler %s 不存在，已忽略", plugin_key, spec.handler)
                continue
            chains.for_stage(spec.stage).append(_Link(spec=spec, inst=inst, ctx=ctx))
    for stage in STAGES:
        links = sorted(chains.for_stage(stage), key=lambda link: (link.spec.order, link.spec.plugin_key, link.spec.index))
        if len(links) > MAX_CHAIN_LENGTH:
            extra = links[MAX_CHAIN_LENGTH:]
            chains.dropped.extend(f"{stage}:{link.spec.name}" for link in extra)
            log.warning(
                "账号 %s 的 %s 中间件超过 %s 个，以下不生效：%s",
                getattr(state, "account_id", None),
                stage,
                MAX_CHAIN_LENGTH,
                ", ".join(link.spec.name for link in extra),
            )
            links = links[:MAX_CHAIN_LENGTH]
        setattr(chains, stage, links)
    try:
        state._tp_middleware_chains = chains
    except Exception:  # noqa: BLE001
        pass
    return chains


# ─────────────────────────────────────────────────────
# 单次调用：超时 / 熔断 / 统计 / 阻塞检测
# ─────────────────────────────────────────────────────
@dataclass
class MiddlewareCall:
    """传给中间件钩子的第三个参数 ``mw``。"""

    stage: str
    plugin_key: str
    account_id: int | None
    action_type: str | None = None
    event_type: str | None = None
    source_plugin_key: str | None = None
    channels: tuple[str, ...] = ()
    trace_id: str | None = None

    @property
    def is_own(self) -> bool:
        """当前动作是不是本插件自己发的。"""

        return self.source_plugin_key == self.plugin_key

    def reject(self, code: str = "rejected", message: str = "") -> None:
        """outbound：拒绝发送当前动作（动作记录与插件结果里 error_code=middleware_rejected）。"""

        if self.stage not in (STAGE_OUTBOUND,):
            raise RuntimeError("mw.reject 只能在 outbound 中间件里用")
        raise MiddlewareReject(code, message)

    def drop(self, reason: str = "") -> None:
        """inbound：丢弃当前事件，后续中间件与插件都收不到。"""

        if self.stage != STAGE_INBOUND:
            raise RuntimeError("mw.drop 只能在 inbound 中间件里用")
        raise MiddlewareDrop(reason)

    @staticmethod
    def thaw(value: Any) -> Any:
        """只读视图 → 可修改的独立副本（改完整体赋回动作字段）。"""

        return thaw(value)


def _circuit_open(state: Any, plugin_key: str) -> bool:
    circuit = (getattr(state, "plugin_circuits", None) or {}).get(plugin_key)
    return circuit is not None and float(circuit.get("opened_until") or 0) > time.monotonic()


def _update_circuit(state: Any, plugin_key: str, *, ok: bool) -> None:
    circuits = getattr(state, "plugin_circuits", None)
    if circuits is None:
        return
    from ...settings import settings as app_settings

    entry = plugin_stats.update_circuit(
        circuits.get(plugin_key),
        ok=ok,
        now=time.monotonic(),
        threshold=max(1, int(app_settings.plugin_circuit_failure_threshold or 3)),
        cooldown=max(1.0, float(app_settings.plugin_circuit_cooldown_seconds or 60.0)),
    )
    if entry is None:
        circuits.pop(plugin_key, None)
    else:
        circuits[plugin_key] = entry


def _record(state: Any, spec: MiddlewareSpec, dim: str | None, outcome: str, started: float, error: BaseException | str | None) -> None:
    redis = getattr(state, "redis", None)
    if redis is not None:
        plugin_stats.get_recorder().set_redis_factory(lambda _redis=redis: _redis)
    plugin_stats.record_hook(
        account_id=getattr(state, "account_id", None),
        plugin_key=spec.plugin_key,
        hook=f"middleware.{spec.stage}",
        event_type=dim,
        outcome=outcome,
        duration_ms=(time.monotonic() - started) * 1000,
        error=error,
    )


async def _span(trace: Any, spec: MiddlewareSpec, *, status: str, reason_code: str | None, error: str | None, dim: str | None, started: float) -> None:
    if not trace:
        return
    try:
        from ...services.event_trace import record_span

        await record_span(
            trace,
            "plugin_middleware",
            status,
            component="plugin_middleware",
            plugin_key=spec.plugin_key,
            hook=spec.handler,
            stage=spec.stage,
            event_type=dim,
            reason_code=reason_code,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:  # noqa: BLE001 - Trace 不能影响投递
        log.debug("记录中间件 span 失败", exc_info=True)


class _Outcome:
    __slots__ = ("value", "ok", "reject", "drop", "skipped")

    def __init__(self) -> None:
        self.value: Any = None
        self.ok = False
        self.reject: MiddlewareReject | None = None
        self.drop: MiddlewareDrop | None = None
        self.skipped = False


async def _invoke(
    state: Any,
    link: _Link,
    value: Any,
    mw: MiddlewareCall,
    *,
    dim: str | None,
    timeout_ms: int,
    trace: Any,
) -> _Outcome:
    spec = link.spec
    outcome = _Outcome()
    started = time.monotonic()
    if _circuit_open(state, spec.plugin_key):
        outcome.skipped = True
        _record(state, spec, dim, plugin_stats.OUTCOME_CIRCUIT_OPEN, started, None)
        return outcome
    timeout = max(0.01, timeout_ms / 1000)
    try:
        handler = getattr(link.inst, spec.handler)
        result = handler(link.ctx, value, mw)
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            async with asyncio.timeout(timeout):
                result = await blocking_monitor.probe(
                    result,
                    account_id=getattr(state, "account_id", None),
                    plugin_key=spec.plugin_key,
                    hook=f"middleware.{spec.stage}",
                    event_type=dim,
                )
        if time.monotonic() - started > timeout:
            # 同步阻塞超过时限（asyncio 打断不了不 await 的代码）：结果作废，按超时算。
            raise TimeoutError(f"PLUGIN_INVOKE_TIMEOUT: middleware {spec.name}")
    except MiddlewareReject as exc:
        outcome.ok = True
        outcome.reject = exc
        _record(state, spec, dim, plugin_stats.OUTCOME_OK, started, None)
        _update_circuit(state, spec.plugin_key, ok=True)
        return outcome
    except MiddlewareDrop as exc:
        outcome.ok = True
        outcome.drop = exc
        _record(state, spec, dim, plugin_stats.OUTCOME_OK, started, None)
        _update_circuit(state, spec.plugin_key, ok=True)
        return outcome
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail-open
        kind = plugin_stats.OUTCOME_TIMEOUT if isinstance(exc, TimeoutError) else plugin_stats.OUTCOME_FAIL
        if kind == plugin_stats.OUTCOME_TIMEOUT and not str(exc):
            exc = TimeoutError(f"PLUGIN_INVOKE_TIMEOUT: middleware {spec.name} > {timeout_ms}ms")
        _record(state, spec, dim, kind, started, exc)
        _update_circuit(state, spec.plugin_key, ok=False)
        await _span(
            trace,
            spec,
            status="failed",
            reason_code=plugin_stats.reason_code_for_outcome(kind),
            error=plugin_stats.summarize_error(exc),
            dim=dim,
            started=started,
        )
        log.debug("中间件 %s 失败（fail-open，按原样继续）", spec.name, exc_info=True)
        return outcome
    outcome.ok = True
    outcome.value = result
    _record(state, spec, dim, plugin_stats.OUTCOME_OK, started, None)
    _update_circuit(state, spec.plugin_key, ok=True)
    return outcome


async def _violation(state: Any, link: _Link, exc: _Violation, *, dim: str | None, trace: Any) -> None:
    spec = link.spec
    started = time.monotonic()
    # 改了不允许改的字段 / 返回畸形值：算一次失败（计入熔断），修改整体回滚。
    _record(state, spec, dim, plugin_stats.OUTCOME_FAIL, started, f"{exc.reason_code}: {exc}")
    _update_circuit(state, spec.plugin_key, ok=False)
    await _span(trace, spec, status="failed", reason_code=exc.reason_code, error=str(exc), dim=dim, started=started)
    log.warning("中间件 %s 的修改已回滚：%s", spec.name, exc)


# ─────────────────────────────────────────────────────
# outbound
# ─────────────────────────────────────────────────────
def action_channels(action: dict[str, Any]) -> tuple[str, ...]:
    from ...services.interaction.contracts import action_send_via_options

    try:
        options = action_send_via_options(action)
    except Exception:  # noqa: BLE001
        options = []
    return tuple(str(item) for item in options) or ("interaction_bot",)


def _validate_outbound(before: dict[str, Any], after: Any) -> dict[str, Any]:
    if not isinstance(after, Mapping):
        raise _Violation(REASON_MALFORMED, f"outbound 中间件必须返回动作 dict 或 None，收到 {type(after).__name__}")
    result = {key: _unfreeze_shared(item) for key, item in after.items() if not str(key).startswith(_INTERNAL_PREFIX)}
    visible_before = {key for key in before if not str(key).startswith(_INTERNAL_PREFIX)}
    changed: list[str] = []
    for key in visible_before | set(result):
        if key in OUTBOUND_MUTABLE_FIELDS:
            continue
        if key not in result or key not in before:
            changed.append(key)
            continue
        old, new = before[key], result[key]
        if old is new:
            continue
        if old != new:
            changed.append(key)
    if changed:
        raise _Violation(REASON_FIELD_VIOLATION, f"改了不允许改的字段：{', '.join(sorted(changed))}")
    for key in ("text", "caption", "parse_mode"):
        if key in result and result[key] is not None and not isinstance(result[key], str):
            raise _Violation(REASON_MALFORMED, f"{key} 必须是字符串")
    for key in ("rich_message", "reply_markup", core_render.RENDER_PROFILE_KEY, "result", "link_preview_options"):
        if key in result and result[key] is not None and not isinstance(result[key], dict):
            raise _Violation(REASON_MALFORMED, f"{key} 必须是对象")
    if "rich_message" in before and isinstance(before.get("rich_message"), dict) and not isinstance(result.get("rich_message"), dict):
        raise _Violation(REASON_MALFORMED, "rich_message 不能删除")
    if "annotations" in result and result["annotations"] is not None:
        _check_json_size(result["annotations"], "annotations")
    # 内部键（_tp*）原样保留
    for key, item in before.items():
        if str(key).startswith(_INTERNAL_PREFIX):
            result[key] = item
    return result


def _check_json_size(value: Any, name: str) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _Violation(REASON_MALFORMED, f"{name} 只能包含 JSON 值") from exc
    if len(encoded.encode("utf-8")) > ANNOTATION_MAX_BYTES:
        raise _Violation(REASON_MALFORMED, f"{name} 超过 {ANNOTATION_MAX_BYTES} 字节")


def _source_plugin_key(action: dict[str, Any], default: str | None) -> str | None:
    context = action.get("context")
    if isinstance(context, dict):
        value = str(context.get("plugin_key") or "").strip()
        if value:
            return value
    return default


async def run_outbound(
    state: Any,
    actions: list[dict[str, Any]],
    *,
    source_plugin_key: str | None,
    trace: Any = None,
    chains: Chains | None = None,
) -> list[dict[str, Any]]:
    """对已规范化的动作依次执行 outbound 链（插件中间件；core.* 由调用方随后执行）。"""

    chains = chains or chains_for(state)
    links = chains.outbound
    delivered_links = chains.delivered
    if not links and not delivered_links:
        return actions
    out: list[dict[str, Any]] = []
    trace_id = str(getattr(trace, "trace_id", None) or trace or "") or None
    for action in actions:
        action_type = str(action.get("type") or "")
        source = _source_plugin_key(action, source_plugin_key)
        channels = action_channels(action)
        current = action
        started = time.monotonic()
        for link in links:
            spec = link.spec
            if not spec.matches_action(action_type, channels, source):
                continue
            if (time.monotonic() - started) * 1000 >= CHAIN_BUDGET_MS:
                _record(state, spec, action_type, plugin_stats.OUTCOME_SKIPPED, time.monotonic(), "chain budget exceeded")
                continue
            mw = MiddlewareCall(
                stage=STAGE_OUTBOUND,
                plugin_key=spec.plugin_key,
                account_id=getattr(state, "account_id", None),
                action_type=action_type,
                source_plugin_key=source,
                channels=channels,
                trace_id=trace_id,
            )
            result = await _invoke(state, link, _view_of(current), mw, dim=action_type, timeout_ms=spec.timeout_ms, trace=trace)
            if result.reject is not None:
                current = dict(current)
                current["_tp_reject"] = {
                    "error_code": REASON_REJECTED,
                    "error": f"中间件 {spec.plugin_key} 拒绝发送：{result.reject.code}"
                    + (f"（{result.reject.message}）" if result.reject.message else ""),
                    "middleware": spec.plugin_key,
                    "code": result.reject.code,
                }
                await _span(trace, spec, status="failed", reason_code=REASON_REJECTED, error=result.reject.code, dim=action_type, started=time.monotonic())
                break
            if not result.ok or result.value is None:
                continue
            try:
                current = _validate_outbound(current, result.value)
            except _Violation as exc:
                await _violation(state, link, exc, dim=action_type, trace=trace)
                continue
        if delivered_links and any(link.spec.matches_action(action_type, channels, source) for link in delivered_links):
            if current is action:
                current = dict(action)
            current[_DELIVERED_MARK] = True
        out.append(current)
    return out


async def run_stream_preview(
    state: Any,
    text: str,
    *,
    source_plugin_key: str | None,
    chat_id: Any = None,
    trace: Any = None,
) -> str | None:
    """流式中间态（每次节流后的中间编辑）：只跑显式声明了 ``stream_preview`` 的 outbound 中间件。

    每个钩子 50 ms；返回改写后的文本，``mw.reject`` 时返回 None（本次中间编辑跳过）。
    """

    links = [link for link in chains_for(state).outbound if STREAM_PREVIEW_ACTION in link.spec.action_types]
    if not links:
        return text
    current: dict[str, Any] = {"type": STREAM_PREVIEW_ACTION, "text": text, "chat_id": chat_id, "send_via": "interaction_bot"}
    for link in links:
        spec = link.spec
        if not spec.matches_action(STREAM_PREVIEW_ACTION, ("interaction_bot",), source_plugin_key):
            continue
        mw = MiddlewareCall(
            stage=STAGE_OUTBOUND,
            plugin_key=spec.plugin_key,
            account_id=getattr(state, "account_id", None),
            action_type=STREAM_PREVIEW_ACTION,
            source_plugin_key=source_plugin_key,
            channels=("interaction_bot",),
        )
        result = await _invoke(
            state,
            link,
            _view_of(current),
            mw,
            dim=STREAM_PREVIEW_ACTION,
            timeout_ms=min(spec.timeout_ms, STREAM_PREVIEW_TIMEOUT_MS),
            trace=trace,
        )
        if result.reject is not None:
            return None
        if not result.ok or result.value is None:
            continue
        try:
            current = _validate_outbound(current, result.value)
        except _Violation as exc:
            await _violation(state, link, exc, dim=STREAM_PREVIEW_ACTION, trace=trace)
    value = current.get("text")
    return value if isinstance(value, str) else text


# ─────────────────────────────────────────────────────
# inbound
# ─────────────────────────────────────────────────────
@dataclass
class InboundResult:
    payload: dict[str, Any]
    dropped: str | None = None
    dropped_by: str | None = None
    changed: bool = False


def _apply_inbound_patch(payload: dict[str, Any], plugin_key: str, patch: Any) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        raise _Violation(REASON_MALFORMED, f"inbound 中间件必须返回 None 或补丁 dict，收到 {type(patch).__name__}")
    extra = sorted(str(key) for key in patch if key not in INBOUND_PATCH_FIELDS)
    if extra:
        raise _Violation(REASON_FIELD_VIOLATION, f"inbound 补丁只能改 text / annotations，不能改：{', '.join(extra)}")
    out = dict(payload)
    if "text" in patch:
        text = patch["text"]
        if not isinstance(text, str):
            raise _Violation(REASON_MALFORMED, "text 必须是字符串")
        for key in ("message", "source"):
            holder = out.get(key)
            if isinstance(holder, dict) and "text" in holder:
                copied = dict(holder)
                copied["text"] = text
                out[key] = copied
        for key in ("message_text", "text"):
            if key in out and isinstance(out[key], str):
                out[key] = text
    if "annotations" in patch:
        value = _unfreeze_shared(patch["annotations"])
        _check_json_size(value, "annotations")
        annotations = dict(out.get("annotations") or {}) if isinstance(out.get("annotations"), dict) else {}
        annotations[plugin_key] = value
        out["annotations"] = annotations
    return out


async def run_inbound(
    state: Any,
    payload: dict[str, Any],
    *,
    event_type: str | None,
    source: str | None,
    trace: Any = None,
) -> InboundResult:
    links = chains_for(state).inbound
    result = InboundResult(payload=payload)
    if not links:
        return result
    started = time.monotonic()
    trace_id = str(getattr(trace, "trace_id", None) or trace or "") or None
    for link in links:
        spec = link.spec
        if not spec.matches_event(event_type, source):
            continue
        if (time.monotonic() - started) * 1000 >= CHAIN_BUDGET_MS:
            _record(state, spec, event_type, plugin_stats.OUTCOME_SKIPPED, time.monotonic(), "chain budget exceeded")
            continue
        mw = MiddlewareCall(
            stage=STAGE_INBOUND,
            plugin_key=spec.plugin_key,
            account_id=getattr(state, "account_id", None),
            event_type=event_type,
            trace_id=trace_id,
        )
        outcome = await _invoke(state, link, FrozenDict(result.payload), mw, dim=event_type, timeout_ms=spec.timeout_ms, trace=trace)
        if outcome.drop is not None:
            result.dropped = outcome.drop.reason
            result.dropped_by = spec.plugin_key
            try:
                from ...services.event_trace import record_span

                if trace:
                    await record_span(
                        trace,
                        "plugin_middleware",
                        "skipped",
                        component="plugin_middleware",
                        plugin_key=spec.plugin_key,
                        stage=STAGE_INBOUND,
                        event_type=event_type,
                        reason_code=REASON_DROPPED,
                        message=f"inbound 中间件丢弃了事件：{outcome.drop.reason}",
                    )
            except Exception:  # noqa: BLE001
                log.debug("记录 inbound drop span 失败", exc_info=True)
            return result
        if not outcome.ok or outcome.value is None:
            continue
        try:
            result.payload = _apply_inbound_patch(result.payload, spec.plugin_key, outcome.value)
            result.changed = True
        except _Violation as exc:
            await _violation(state, link, exc, dim=event_type, trace=trace)
    return result


# ─────────────────────────────────────────────────────
# delivered
# ─────────────────────────────────────────────────────
_BACKGROUND: set[asyncio.Task[Any]] = set()


def wants_delivered(action: dict[str, Any]) -> bool:
    return bool(isinstance(action, dict) and action.get(_DELIVERED_MARK))


def delivered_summary(action: dict[str, Any], *, ok: bool, channel: str | None = None) -> dict[str, Any]:
    """投递结果摘要（不含正文）：给 delivered 中间件与跨进程 IPC 用。"""

    context = action.get("context") if isinstance(action.get("context"), dict) else {}
    error = action.get("_tp_error") if isinstance(action.get("_tp_error"), dict) else None
    rejection = action.get("_tp_reject") if isinstance(action.get("_tp_reject"), dict) else None
    summary: dict[str, Any] = {
        "action_type": str(action.get("type") or ""),
        "ok": bool(ok),
        "chat_id": action.get("chat_id"),
        "source_plugin_key": str(context.get("plugin_key") or "") or None,
        "entry_key": str(context.get("entry_key") or "") or None,
        "trace_id": str(context.get("trace_id") or "") or None,
        "channel": channel or action_channels(action)[0],
        "error_code": None,
    }
    if not ok:
        summary["error_code"] = str((rejection or error or {}).get("error_code") or "action_failed")
    if action.get("_tp_inline_message_id"):
        summary["inline_message_id"] = str(action.get("_tp_inline_message_id"))
    return summary


async def run_delivered(state: Any, summaries: list[dict[str, Any]], *, trace: Any = None) -> int:
    """依次把投递结果交给 delivered 中间件；返回调用次数。"""

    links = chains_for(state).delivered
    calls = 0
    for summary in summaries:
        action_type = str(summary.get("action_type") or "")
        source = summary.get("source_plugin_key")
        channels = (str(summary.get("channel") or "interaction_bot"),)
        started = time.monotonic()
        for link in links:
            spec = link.spec
            if not spec.matches_action(action_type, channels, source):
                continue
            if (time.monotonic() - started) * 1000 >= CHAIN_BUDGET_MS:
                _record(state, spec, action_type, plugin_stats.OUTCOME_SKIPPED, time.monotonic(), "chain budget exceeded")
                continue
            mw = MiddlewareCall(
                stage=STAGE_DELIVERED,
                plugin_key=spec.plugin_key,
                account_id=getattr(state, "account_id", None),
                action_type=action_type,
                source_plugin_key=source,
                trace_id=summary.get("trace_id"),
            )
            await _invoke(state, link, FrozenDict(dict(summary)), mw, dim=action_type, timeout_ms=spec.timeout_ms, trace=trace)
            calls += 1
    return calls


def schedule_delivered(state: Any, summaries: list[dict[str, Any]], *, trace: Any = None) -> asyncio.Task[Any] | None:
    """异步执行 delivered 链，不阻塞投递。"""

    if not summaries or not chains_for(state).delivered:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(run_delivered(state, summaries, trace=trace))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


# ─────────────────────────────────────────────────────
# 目录（API / 文档）
# ─────────────────────────────────────────────────────
def limits_catalog() -> dict[str, Any]:
    return {
        "stages": list(STAGES),
        "default_timeout_ms": DEFAULT_TIMEOUT_MS,
        "min_timeout_ms": MIN_TIMEOUT_MS,
        "max_timeout_ms": MAX_TIMEOUT_MS,
        "stream_preview_timeout_ms": STREAM_PREVIEW_TIMEOUT_MS,
        "chain_budget_ms": CHAIN_BUDGET_MS,
        "max_chain_length": MAX_CHAIN_LENGTH,
        "order_min": ORDER_MIN,
        "order_max": ORDER_MAX,
        "outbound_action_types": sorted(OUTBOUND_ACTION_TYPES),
        "outbound_mutable_fields": sorted(OUTBOUND_MUTABLE_FIELDS),
        "inbound_patch_fields": sorted(INBOUND_PATCH_FIELDS),
    }


def build_bench_state(handlers: list[tuple[str, Any]]) -> Any:
    """性能基准 / 测试用：用 (plugin_key, async 函数) 列表拼一个只有 outbound 中间件的账号运行态。"""

    from types import SimpleNamespace

    instances: dict[str, Any] = {}
    contexts: dict[str, Any] = {}
    for index, (plugin_key, func) in enumerate(handlers):
        manifest = SimpleNamespace(
            key=plugin_key,
            permissions=[MIDDLEWARE_PERMISSION],
            middleware=[
                {
                    "stage": "outbound",
                    "handler": "on_outbound",
                    "order": 100 + index,
                    "match": {"sources": ["any_plugin"]},
                }
            ],
            render_profile=None,
        )

        async def _method(self: Any, ctx: Any, action: Any, mw: Any, _func: Any = func) -> Any:
            return await _func(ctx, action, mw)

        cls = type(f"Bench{index}", (), {"on_outbound": _method, "_manifest": manifest})
        instances[plugin_key] = cls()
        contexts[plugin_key] = SimpleNamespace(account_id=1)
    return SimpleNamespace(account_id=1, generation=1, instances=instances, contexts=contexts, plugin_circuits={}, redis=None)


__all__ = [
    "CHAIN_BUDGET_MS",
    "Chains",
    "DEFAULT_TIMEOUT_MS",
    "FrozenDict",
    "FrozenList",
    "InboundResult",
    "MAX_CHAIN_LENGTH",
    "MIDDLEWARE_PERMISSION",
    "MiddlewareCall",
    "MiddlewareDeclarationError",
    "MiddlewareDrop",
    "MiddlewareReject",
    "MiddlewareSpec",
    "OUTBOUND_MUTABLE_FIELDS",
    "STAGES",
    "build_bench_state",
    "chains_for",
    "delivered_summary",
    "freeze",
    "limits_catalog",
    "parse_middleware",
    "run_delivered",
    "run_inbound",
    "run_outbound",
    "run_stream_preview",
    "schedule_delivered",
    "thaw",
    "wants_delivered",
]
