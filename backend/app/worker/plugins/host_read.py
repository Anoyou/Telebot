"""``ctx.host.read``：声明式只读宿主查询面（Plugin API 1.1，0.108.0+）。

插件以前要拿宿主数据（管理员名单、已启用模型、允许会话……）只能等宿主往 Event Bus payload
里加字段（例如 0.106.0-beta.14 的 ``owner_user_ids`` / ``admin_user_ids``）。现在改为声明 + 查询::

    # plugin.json
    "reads": ["account.admins@1", "ai.models@1"]

    # 插件代码
    admins = await ctx.host.read("account.admins@1")
    models = await ctx.host.read("ai.models@1", enabled_only=True)

- 资源名带版本（``名称@版本``），结构变化发新版本号，旧版本继续可读；
- 只能读 ``reads`` 里声明过的资源，未声明或未知资源抛 :class:`HostReadError`（``PermissionError`` 子类）；
- 只读、只返回 JSON 值，不含密钥、手机号、会话正文等敏感数据；
- 每个资源同时登记为能力 ``host.read.<名称>@<版本>``，可用 ``ctx.capabilities.has(...)`` 探测。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class HostReadError(PermissionError):
    """资源未声明、未知或当前上下文不可用。"""


@dataclass(frozen=True)
class HostResource:
    name: str
    version: int
    summary: str
    since: str
    params: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "since": self.since,
            "params": list(self.params),
        }


_SINCE = "0.108.0-beta.1"

HOST_RESOURCES: tuple[HostResource, ...] = (
    HostResource(
        name="account.admins",
        version=1,
        since=_SINCE,
        summary="账号本人与 TelePilot 管理员（本人 + 授权 sudo 用户）的 Telegram user id：{owner_user_ids, admin_user_ids}",
    ),
    HostResource(
        name="ai.models",
        version=1,
        since=_SINCE,
        params=("enabled_only",),
        summary=(
            "插件可用的模型清单（需 ai_text / ai_agent 权限）：[{provider_id, provider_name, provider, model, enabled, default, tags,"
            " display_name, label, available, has_api_key, cost_tier, cost_tier_label}]；后 6 个字段 0.111.0 起（能力 ai.models.metadata）"
        ),
    ),
    HostResource(
        name="chat.allowed",
        version=1,
        since=_SINCE,
        summary="账号「允许会话」：{all: bool, chat_ids: [...]}；all=true 表示未限制（全部会话）",
    ),
    HostResource(
        name="plugin.stats",
        version=1,
        since=_SINCE,
        summary="本插件在当前账号的钩子统计（与 GET /api/plugins/{key}/stats 同结构，只含本账号）",
    ),
)

COST_TIER_LABELS = {1: "便宜", 2: "标准", 3: "旗舰"}


def ai_model_metadata(provider: Any, model: Mapping[str, Any], model_id: str, enabled: bool) -> dict[str, Any]:
    """``ai.models@1`` 0.111.0 新增的只增字段：显示名、可用状态（有可用 Key 且模型启用）、成本档。

    ``provider`` 是脱敏后的 ``AIProviderInfo``（不含任何密钥），``has_api_key`` 只是布尔值。
    """

    display = ""
    for key in ("display_name", "name", "label"):
        value = model.get(key) if isinstance(model, Mapping) else None
        if isinstance(value, str) and value.strip():
            display = value.strip()
            break
    display = display or model_id
    has_key = bool(getattr(provider, "has_api_key", False))
    try:
        tier = int(getattr(provider, "cost_tier", 2) or 2)
    except (TypeError, ValueError):
        tier = 2
    tier = min(3, max(1, tier))
    return {
        "display_name": display,
        "label": f"{getattr(provider, 'name', '')} · {display}",
        "available": bool(has_key and enabled),
        "has_api_key": has_key,
        "cost_tier": tier,
        "cost_tier_label": COST_TIER_LABELS[tier],
    }


HOST_RESOURCE_INDEX: dict[str, HostResource] = {item.ref: item for item in HOST_RESOURCES}


def normalize_resource_ref(value: Any) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return text
    name, _, version = text.partition("@")
    return f"{name.strip()}@{version.strip()}"


def validate_reads(values: Iterable[Any] | None) -> tuple[list[str], list[str]]:
    """校验 manifest ``reads``：返回 ``(有效资源, 未知资源)``。"""

    valid: list[str] = []
    unknown: list[str] = []
    for raw in values or []:
        ref = normalize_resource_ref(raw)
        if not ref:
            continue
        if ref in HOST_RESOURCE_INDEX:
            if ref not in valid:
                valid.append(ref)
        else:
            unknown.append(ref)
    return valid, unknown


Resolver = Callable[..., Awaitable[Any]]


class PluginHost:
    """``ctx.host``：每个 [账号 × 插件] 一个实例，按 manifest ``reads`` 控制可读资源。"""

    __slots__ = ("_account_id", "_declared", "_plugin_key", "_resolvers")

    def __init__(
        self,
        *,
        plugin_key: str,
        account_id: int,
        declared: Iterable[Any] | None,
        resolvers: dict[str, Resolver],
    ) -> None:
        self._plugin_key = str(plugin_key)
        self._account_id = int(account_id)
        self._declared = frozenset(validate_reads(declared)[0])
        self._resolvers = dict(resolvers)

    @property
    def declared(self) -> list[str]:
        return sorted(self._declared)

    def available(self) -> list[str]:
        """当前上下文可读的资源（已声明且宿主提供）。"""

        return sorted(ref for ref in self._declared if ref in self._resolvers)

    def can_read(self, resource: str) -> bool:
        return normalize_resource_ref(resource) in self.available()

    async def read(self, resource: str, **params: Any) -> Any:
        ref = normalize_resource_ref(resource)
        spec = HOST_RESOURCE_INDEX.get(ref)
        if spec is None:
            known = ", ".join(sorted(HOST_RESOURCE_INDEX))
            raise HostReadError(f"未知宿主资源 {ref!r}（可用：{known}）")
        if ref not in self._declared:
            raise HostReadError(f"插件 {self._plugin_key} 未在 plugin.json reads 中声明 {ref}")
        resolver = self._resolvers.get(ref)
        if resolver is None:
            raise HostReadError(f"当前上下文不提供 {ref}")
        unknown = sorted(set(params) - set(spec.params))
        if unknown:
            raise HostReadError(f"{ref} 不支持参数：{', '.join(unknown)}")
        return await resolver(**params)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"PluginHost(plugin={self._plugin_key!r}, reads={self.declared!r})"


def host_resource_catalog() -> list[dict[str, Any]]:
    return [item.to_dict() for item in HOST_RESOURCES]


__all__ = [
    "HOST_RESOURCES",
    "HOST_RESOURCE_INDEX",
    "HostReadError",
    "HostResource",
    "PluginHost",
    "host_resource_catalog",
    "normalize_resource_ref",
    "validate_reads",
]
