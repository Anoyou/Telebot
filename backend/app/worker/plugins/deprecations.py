"""插件接口弃用登记：每个已弃用接口登记一次，调用时记录警告与次数。

用法（宿主侧）::

    from .deprecations import note_deprecated
    note_deprecated("ai.tag_alias", plugin_key=self.plugin_key, account_id=self.account_id)

- 每个 [进程 × 插件 × 弃用项] 每 :data:`WARN_INTERVAL_SECONDS` 最多写一条 WARNING 日志，避免刷屏；
- 每次调用都计数（复用 :mod:`.plugin_stats` 的 Redis 汇总），插件详情页与
  ``GET /api/plugins/{key}/stats`` 的 ``deprecations`` 字段可见；
- 移除计划：登记的 ``removal`` 写的是计划移除的 Plugin API 版本，移除前至少提前一个 minor 宿主版本公告，
  并以使用量统计为依据；``GET /api/plugins/capabilities`` 列出全部登记项。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .plugin_stats import get_recorder

log = logging.getLogger(__name__)

WARN_INTERVAL_SECONDS = 6 * 3600


@dataclass(frozen=True)
class Deprecation:
    id: str
    summary: str
    replacement: str
    deprecated_since: str
    removal: str = "Plugin API 2.0"
    doc: str = "docs/PLUGIN-API-REFERENCE.md#弃用登记"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "replacement": self.replacement,
            "deprecated_since": self.deprecated_since,
            "removal": self.removal,
            "doc": self.doc,
        }


DEPRECATIONS: tuple[Deprecation, ...] = (
    Deprecation(
        id="hook.on_interaction",
        summary="只实现 on_interaction(ctx, entry_key, payload)（交互入口迁移桥），没有实现 on_event",
        replacement="实现 on_event(ctx, payload)，入口信息在 payload['trigger'] / payload['entry_key']",
        deprecated_since="0.107.0",
    ),
    Deprecation(
        id="manifest.min_telebot_version",
        summary="plugin.json / manifest 使用旧字段 min_telebot_version",
        replacement="min_telepilot_version（可带 beta 号，如 0.106.0-beta.12）",
        deprecated_since="0.15.0",
    ),
    Deprecation(
        id="ai.tag_alias",
        summary="ctx.ai.complete / stream_complete 使用 tag= / tags= 兼容别名",
        replacement="provider_tag=",
        deprecated_since="0.25.0",
    ),
    Deprecation(
        id="action.send_via_notice",
        summary="动作 send_via / channel 使用已下线的 notice / bbot_notice / notice_bot 通道（动作会失败）",
        replacement="send_via: interaction_bot 或 userbot_reply",
        deprecated_since="0.36.0",
        removal="已下线（只保留失败提示与计数）",
    ),
    Deprecation(
        id="markup.raw_callback_data",
        summary="插件动作的 reply_markup 直接写 callback_data（点击按会话范围投递，可能被其它插件或规则收到）",
        replacement='按钮写 {"text": ..., "callback": {"data": ...}}，订阅 scope "own_buttons"（宿主签发 token 并路由回本插件）',
        deprecated_since="0.108.0",
        removal="Plugin API 2.0（至少保留到 0.109.0 之后）",
        doc="docs/PLUGIN-API-REFERENCE.md#通用回调路由own_buttons",
    ),
    Deprecation(
        id="payload.admin_user_ids",
        summary="从 Event Bus payload 的 owner_user_ids / admin_user_ids 读取管理员名单（0.106.0-beta.14 临时字段）",
        replacement='plugin.json 声明 reads: ["account.admins@1"]，用 await ctx.host.read("account.admins@1")',
        deprecated_since="0.108.0",
        removal="Plugin API 2.0（字段继续下发，只做登记，无法统计读取次数）",
        doc="docs/PLUGIN-API-REFERENCE.md#宿主只读查询ctxhostread",
    ),
)

DEPRECATION_INDEX: dict[str, Deprecation] = {item.id: item for item in DEPRECATIONS}
if len(DEPRECATION_INDEX) != len(DEPRECATIONS):  # pragma: no cover - 注册表自检
    raise RuntimeError("插件弃用登记存在重复 id")

_LAST_WARNED: dict[tuple[str, str], float] = {}


def note_deprecated(
    deprecation_id: str,
    *,
    plugin_key: str | None,
    account_id: int | None = None,
    detail: str | None = None,
    now: float | None = None,
) -> bool:
    """记录一次弃用接口调用；返回本次是否写了 WARNING 日志。永不抛异常。"""

    try:
        item = DEPRECATION_INDEX.get(deprecation_id)
        if item is None:
            log.debug("未登记的弃用项 %s（plugin=%s）", deprecation_id, plugin_key)
            return False
        key = str(plugin_key or "").strip() or "<unknown>"
        get_recorder().record_deprecation(
            account_id=account_id,
            plugin_key=key,
            deprecation_id=item.id,
            detail=detail,
            now=now,
        )
        stamp = time.monotonic() if now is None else now
        last = _LAST_WARNED.get((key, item.id))
        if last is not None and stamp - last < WARN_INTERVAL_SECONDS:
            return False
        _LAST_WARNED[(key, item.id)] = stamp
        log.warning(
            "插件 %s 使用了已弃用接口 %s：%s；请改用 %s（计划移除：%s）",
            key,
            item.id,
            item.summary,
            item.replacement,
            item.removal,
        )
        return True
    except Exception:  # noqa: BLE001 - 弃用登记不能影响插件
        log.debug("记录弃用接口调用失败", exc_info=True)
        return False


def deprecation_catalog() -> list[dict[str, Any]]:
    return [item.to_dict() for item in DEPRECATIONS]


def reset_warn_state() -> None:
    _LAST_WARNED.clear()


__all__ = [
    "DEPRECATIONS",
    "DEPRECATION_INDEX",
    "Deprecation",
    "deprecation_catalog",
    "note_deprecated",
    "reset_warn_state",
]
