"""出站渲染的平台内置中间件（``core.*``）与 ``render_profile`` 声明（0.109.0，Plugin API 1.2）。

以前表格样式散落在三处写死的调用点：插件动作规范化里的 ``compact_tables``、交互 Bot 出口
``_call_rich_bot_api`` 与 Userbot Rich Message 出口的默认边框 + 斑马纹。现在它们都是登记在
:data:`CORE_MIDDLEWARE` 里的 ``core.*`` 中间件，``rich_compact`` 的转换函数只被本模块调用：

- ``core.render_profile``（outbound，order 900）：合并 manifest / 动作上的 ``render_profile``
  （旧写法 ``compact_tables: true`` 等价于 ``{"table": {"compact": true}}``），从动作上摘掉，
  不发给 Telegram；
- ``core.rich_compact``（outbound，order 950）：按 ``render_profile.table`` 写表格样式——显式的
  ``bordered`` / ``striped`` 值与 ``compact``（Bot API 10.3 紧凑表格）；
- ``core.rich_table_defaults``（transport）：发送出口给没写样式的表格补边框 + 斑马纹（平台默认）；
  旧 Bot API / layer 拒绝紧凑表格时去掉 ``compact`` 重发一次；
- ``core.html_fallback``（transport）：Telegram 拒绝 HTML 实体时退回纯文本重发一次；
- ``core.guest_merge``（delivery）：访客模式（Guest Mode）把消息类动作合并成一次 ``answerGuestQuery``。

平台中间件顺序固定、不能卸载；插件中间件的 ``order`` 限制在 1–899，因此总在 ``core.render_profile``
之前运行，插件中间件新增或改写的表格同样会被统一样式化。

没有声明 ``render_profile`` 的动作与 0.108.0 的输出逐字节一致（golden 测试
``test_rich_render_golden.py`` 守护）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from . import rich_compact as _rc

log = logging.getLogger(__name__)

RENDER_PROFILE_KEY = "render_profile"
COMPACT_TABLES_ACTION_KEY = _rc.COMPACT_TABLES_ACTION_KEY
TABLE_STYLE_KEYS: tuple[str, ...] = ("compact", "bordered", "striped")

STAGE_INBOUND = "inbound"
STAGE_OUTBOUND = "outbound"
STAGE_DELIVERED = "delivered"
STAGE_TRANSPORT = "transport"
STAGE_DELIVERY = "delivery"

CORE_PLUGIN_KEY = "core"


@dataclass(frozen=True)
class CoreMiddleware:
    name: str
    stage: str
    order: int
    summary: str
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "order": self.order,
            "critical": self.critical,
            "summary": self.summary,
        }


CORE_MIDDLEWARE: tuple[CoreMiddleware, ...] = (
    CoreMiddleware(
        name="core.render_profile",
        stage=STAGE_OUTBOUND,
        order=900,
        summary="合并 manifest 与动作上的 render_profile（compact_tables: true 是它的旧写法），不发给 Telegram",
    ),
    CoreMiddleware(
        name="core.rich_compact",
        stage=STAGE_OUTBOUND,
        order=950,
        summary="按 render_profile.table 写表格样式：compact（Bot API 10.3 紧凑表格）与显式 bordered / striped",
    ),
    CoreMiddleware(
        name="core.rich_table_defaults",
        stage=STAGE_TRANSPORT,
        order=1000,
        summary="发送出口给未写样式的表格补边框 + 隔行着色（平台默认）；旧 Bot API 拒绝紧凑表格时去掉 compact 重发一次",
    ),
    CoreMiddleware(
        name="core.html_fallback",
        stage=STAGE_TRANSPORT,
        order=1010,
        critical=True,
        summary="Telegram 拒绝 HTML 实体（请求未生效）时退回纯文本重发一次",
    ),
    CoreMiddleware(
        name="core.guest_merge",
        stage=STAGE_DELIVERY,
        order=1020,
        critical=True,
        summary="访客模式把消息类动作合并成一次 answerGuestQuery",
    ),
)
CORE_MIDDLEWARE_INDEX: dict[str, CoreMiddleware] = {item.name: item for item in CORE_MIDDLEWARE}


def core_middleware_catalog() -> list[dict[str, Any]]:
    return [item.to_dict() for item in CORE_MIDDLEWARE]


# ─────────────────────────────────────────────────────
# 统计：core.* 也按 [账号 × "core" × 中间件名] 记一行（与插件钩子同一套统计）
# ─────────────────────────────────────────────────────
def record_core(name: str, *, started: float, ok: bool = True, error: BaseException | None = None, account_id: int | None = None) -> None:
    try:
        from ..worker.plugins import plugin_stats

        plugin_stats.record_hook(
            account_id=account_id,
            plugin_key=CORE_PLUGIN_KEY,
            hook=name,
            outcome=plugin_stats.OUTCOME_OK if ok else plugin_stats.OUTCOME_FAIL,
            duration_ms=(time.monotonic() - started) * 1000,
            error=error,
        )
    except Exception:  # noqa: BLE001 - 统计不能影响投递
        log.debug("记录 core 中间件统计失败 %s", name, exc_info=True)


# ─────────────────────────────────────────────────────
# render_profile 声明
# ─────────────────────────────────────────────────────
class RenderProfileError(ValueError):
    """render_profile 写法不合法（只在 manifest 校验时抛；动作上的不合法值忽略并记日志）。"""


def normalize_render_profile(value: Any, *, strict: bool = False) -> dict[str, Any] | None:
    """校验并规范化 ``render_profile``；返回 ``{"table": {...}}`` 或 None（未声明 / 空）。

    目前只有 ``table``：``compact`` / ``bordered`` / ``striped`` 三个布尔值，未写的项沿用平台默认
    （不紧凑、带边框、带斑马纹）。``strict=True`` 时不合法写法抛 :class:`RenderProfileError`。
    """

    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        if strict:
            raise RenderProfileError("render_profile 必须是对象")
        return None
    unknown = sorted(str(key) for key in value if key != "table")
    if unknown and strict:
        raise RenderProfileError(f"render_profile 不认识的字段：{', '.join(unknown)}（目前只有 table）")
    table = value.get("table")
    if table in (None, {}):
        return None
    if not isinstance(table, dict):
        if strict:
            raise RenderProfileError("render_profile.table 必须是对象")
        return None
    out: dict[str, bool] = {}
    for key, item in table.items():
        if key not in TABLE_STYLE_KEYS:
            if strict:
                raise RenderProfileError(
                    f"render_profile.table 不认识的字段：{key}（允许：{', '.join(TABLE_STYLE_KEYS)}）"
                )
            continue
        if not isinstance(item, bool):
            if strict:
                raise RenderProfileError(f"render_profile.table.{key} 必须是 true / false")
            continue
        out[key] = item
    return {"table": out} if out else None


def merge_render_profiles(*profiles: dict[str, Any] | None) -> dict[str, Any] | None:
    """后面的覆盖前面的（manifest < 动作 < 中间件改写后的动作）。"""

    table: dict[str, bool] = {}
    for profile in profiles:
        normalized = normalize_render_profile(profile)
        if normalized:
            table.update(normalized["table"])
    return {"table": table} if table else None


def absorb_legacy_compact(action: dict[str, Any]) -> None:
    """``compact_tables: true``（0.106.0-beta.12 起的动作级开关）→ ``render_profile.table.compact``。

    ``compact_tables`` 只在平台内部消费，永远不发给 Telegram；非 True 的值等于没写。
    """

    if COMPACT_TABLES_ACTION_KEY not in action:
        return
    enabled = action.pop(COMPACT_TABLES_ACTION_KEY, None) is True
    if not enabled:
        return
    current = action.get(RENDER_PROFILE_KEY)
    merged = merge_render_profiles({"table": {"compact": True}}, current if isinstance(current, dict) else None)
    if merged:
        action[RENDER_PROFILE_KEY] = merged


# ─────────────────────────────────────────────────────
# outbound：core.render_profile + core.rich_compact
# ─────────────────────────────────────────────────────
def apply_outbound_core(
    action: dict[str, Any],
    *,
    manifest_profile: dict[str, Any] | None = None,
    account_id: int | None = None,
) -> dict[str, Any]:
    """在动作上执行 ``core.render_profile`` → ``core.rich_compact``（原地修改并返回同一个 dict）。

    没有任何声明时只摘掉 ``render_profile`` / ``compact_tables`` 键，rich_message 原样保留
    （默认边框 + 斑马纹由出口的 ``core.rich_table_defaults`` 统一补，与 0.108.0 一致）。
    """

    absorb_legacy_compact(action)
    has_action_profile = RENDER_PROFILE_KEY in action
    raw_profile = action.pop(RENDER_PROFILE_KEY, None)
    if not has_action_profile and not manifest_profile:
        return action
    rich_message = action.get("rich_message")
    if not isinstance(rich_message, dict):
        return action
    started = time.monotonic()
    profile = merge_render_profiles(manifest_profile, raw_profile if isinstance(raw_profile, dict) else None)
    if not profile:
        return action
    table = profile["table"]
    try:
        styled = _rc.apply_table_style(rich_message, bordered=table.get("bordered"), striped=table.get("striped"))
        if table.get("compact") is True:
            styled = _rc.apply_compact_tables(styled)
    except Exception as exc:  # noqa: BLE001 - 格式增强失败时按原样发送（与 0.108.0 一致）
        log.warning("render_profile 表格样式转换失败，按原样发送 rich_message", exc_info=True)
        record_core("core.rich_compact", started=started, ok=False, error=exc, account_id=account_id)
        return action
    action["rich_message"] = styled
    record_core("core.rich_compact", started=started, account_id=account_id)
    return action


# ─────────────────────────────────────────────────────
# transport：core.rich_table_defaults（发送出口）
# ─────────────────────────────────────────────────────
def apply_transport_table_defaults(rich_message: Any) -> Any:
    """出口默认样式：给没写样式的表格补边框 + 斑马纹（调用方显式值优先，见 ``rich_compact``）。"""

    return _rc.apply_table_style_defaults(rich_message)


def strip_compact_for_retry(rich_message: dict[str, Any]) -> dict[str, Any]:
    """旧 Bot API / layer 拒绝紧凑表格后的降级：只去掉 compact，保留边框与斑马纹。"""

    return _rc.strip_compact_tables(rich_message)


def has_compact_tables(rich_message: Any) -> bool:
    return _rc.rich_message_has_compact_tables(rich_message)


def is_compact_unsupported_error(exc: BaseException | str) -> bool:
    return _rc.is_compact_unsupported_error(exc)


__all__ = [
    "COMPACT_TABLES_ACTION_KEY",
    "CORE_MIDDLEWARE",
    "CORE_MIDDLEWARE_INDEX",
    "CORE_PLUGIN_KEY",
    "RENDER_PROFILE_KEY",
    "RenderProfileError",
    "absorb_legacy_compact",
    "apply_outbound_core",
    "apply_transport_table_defaults",
    "core_middleware_catalog",
    "has_compact_tables",
    "is_compact_unsupported_error",
    "merge_render_profiles",
    "normalize_render_profile",
    "strip_compact_for_retry",
]
