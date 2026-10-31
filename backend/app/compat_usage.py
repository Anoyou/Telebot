"""兼容层使用统计（第 22 轮）：每个兼容层入口命中时按「兼容项 × 插件」计数，只统计、不告警。

背景：docs/PLUGIN-API-1.0-PLAN.md 第 3 节列出宿主为旧插件 / 旧数据保留的 20 项兼容层（C01–C20），
其中 14 项此前没有任何调用计数，无法判断“还有没有人在用”。本模块给每个兼容层入口补一个计数器：

- **只计数**：不写 WARNING 日志、不登记弃用、不改变任何行为，插件作者和群用户都看不到；
- **热路径开销极小**：命中时只做一次 dict 查找 + 两次整数 / 浮点赋值；任何异常都吞掉；
- 计数先累加在进程内存里，由 :mod:`app.services.plugin_compat_usage` 定期批量写入数据库
  （``plugin_compat_usage`` 表，SQLite 与 PostgreSQL 通用），主进程、各账号 worker 子进程各自写入、
  数据库里累加，因此跨进程、跨重启都能累计；
- 管理员通过 ``GET /api/plugins/compat-usage`` 读取（Web「插件 → 运行诊断」的只读卡片）。

本模块只依赖标准库，``app.plugin_compat`` 等底层模块可以直接导入。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# 进程内最多跟踪的 [兼容项 × 插件] 行；兼容项是固定 20 个，正常远远到不了。
MAX_PENDING_ROWS = 2000
PLUGIN_KEY_MAX_CHARS = 128
# 与具体插件无关的命中（宿主自身的兜底，如没有安装转账来源插件时的内置解析）记在这个占位 key 下。
HOST_PLUGIN_KEY = "-"


@dataclass(frozen=True)
class CompatItem:
    id: str
    name: str
    trackable: bool = True
    # 计数口径（大白话），管理员在卡片上看到的“什么时候 +1”
    counts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "trackable": self.trackable, "counts": self.counts}


# 与 docs/PLUGIN-API-1.0-PLAN.md 第 3 节、test_plugin_compat_layer.COMPAT_LAYER 一一对应。
COMPAT_ITEMS: tuple[CompatItem, ...] = (
    CompatItem(
        "C01",
        "ctx.messages 旧能力属性（rich_table_compact 等 6 个）",
        trackable=False,
        counts="无法计数：插件读的是类属性（getattr），宿主拦截不到；改成可计数会拖慢每次读取",
    ),
    CompatItem("C02", "on_interaction 交互入口迁移桥", counts="交互入口改走插件的 on_interaction 时 +1"),
    CompatItem("C03", "min_telebot_version 旧字段", counts="加载插件时发现它只写了旧字段 +1"),
    CompatItem("C04", "ctx.ai tag= / tags= 别名", counts="插件用 tag= / tags= 调 ctx.ai 时 +1"),
    CompatItem("C05", "动作 send_via 旧 notice 通道", counts="插件动作指定已下线的 notice 通道时 +1"),
    CompatItem("C06", "按钮直接写 callback_data", counts="插件动作的按钮直接写 callback_data 时 +1"),
    CompatItem(
        "C07",
        "Event Bus payload 的 owner_user_ids / admin_user_ids",
        trackable=False,
        counts="无法计数：字段随事件下发，插件读不读宿主看不到",
    ),
    CompatItem("C08", "兜底入口表（旧版游戏插件未声明入口策略）", counts="宿主替未声明的旧版插件查兜底表并命中时 +1"),
    CompatItem("C09", "交互规则旧动作 action=\"math10\"", counts="存量“算数题”旧规则被触发时 +1"),
    CompatItem("C10", "codex_image 旧模型名改写", counts="保存 / 读取配置时把旧模型名改写成新名时 +1"),
    CompatItem("C11", "chatgpt_image 多 Token 旧配置", counts="旧版本没声明 x-secret-list、宿主补声明时 +1"),
    CompatItem("C12", "规则试运行旧协议 _dry_run_match", counts="规则试运行走旧协议时 +1"),
    CompatItem("C13", ",ai 图片后端旧值 codex_image 与无声明回退", counts=",ai 出图按旧模板值或无声明回退到 codex_image 时 +1"),
    CompatItem("C14", "动作 compact_tables: true", counts="插件动作带 compact_tables 时 +1"),
    CompatItem("C15", "UserBot 旧分发 on_message（message_channels）", counts="消息经旧分发交给插件 on_message 时 +1"),
    CompatItem("C16", "on_direct_message 旧返回值", counts="直通插件返回 True / {\"consume\": ...} 旧写法时 +1"),
    CompatItem("C17", "已迁出 Core 的可选插件与旧 installed 目录", counts="从旧目录加载插件、或把旧内置记录转成普通插件时 +1"),
    CompatItem("C18", "账号级旧 global 配置 / Feature.manifest 回读", counts="新配置表缺数据、改读旧位置时 +1"),
    CompatItem("C19", "旧版免签名插件", counts="签名状态未知的旧插件被放行加载时 +1"),
    CompatItem("C20", "宿主内置转账通知解析器", counts="转账通知最终采用宿主内置解析结果时 +1"),
)

COMPAT_INDEX: dict[str, CompatItem] = {item.id: item for item in COMPAT_ITEMS}
if len(COMPAT_INDEX) != len(COMPAT_ITEMS):  # pragma: no cover - 注册表自检
    raise RuntimeError("兼容层清单存在重复编号")

# 弃用登记（deprecations.py）里能对应到兼容层的项：note_deprecated 命中时顺带计数。
DEPRECATION_TO_COMPAT: dict[str, str] = {
    "hook.on_interaction": "C02",
    "manifest.min_telebot_version": "C03",
    "ai.tag_alias": "C04",
    "action.send_via_notice": "C05",
    "markup.raw_callback_data": "C06",
    "payload.admin_user_ids": "C07",
}

# (compat_id, plugin_key) -> [count, first_at, last_at]
Row = list[Any]
_PENDING: dict[tuple[str, str], Row] = {}
_ON_HIT: Callable[[], None] | None = None
_STATE = {"dropped": 0}


def _clean_plugin_key(plugin_key: Any) -> str:
    text = str(plugin_key or "").strip()
    if not text:
        return HOST_PLUGIN_KEY
    return text[:PLUGIN_KEY_MAX_CHARS]


def hit(compat_id: str, plugin_key: Any = None, *, now: float | None = None) -> None:
    """记一次兼容层命中。永不抛异常、不写日志。"""

    try:
        key = (compat_id, _clean_plugin_key(plugin_key))
        stamp = time.time() if now is None else now
        row = _PENDING.get(key)
        if row is None:
            if compat_id not in COMPAT_INDEX:
                return
            if len(_PENDING) >= MAX_PENDING_ROWS:
                _STATE["dropped"] += 1
                return
            _PENDING[key] = [1, stamp, stamp]
        else:
            row[0] += 1
            if stamp > row[2]:
                row[2] = stamp
        callback = _ON_HIT
        if callback is not None:
            callback()
    except Exception:  # noqa: BLE001 - 统计不能影响兼容层本身
        pass


def hit_deprecation(deprecation_id: str, plugin_key: Any = None) -> None:
    compat_id = DEPRECATION_TO_COMPAT.get(deprecation_id)
    if compat_id is not None:
        hit(compat_id, plugin_key)


def drain() -> dict[tuple[str, str], Row]:
    """取走待写入的增量（调用方写库失败时用 :func:`requeue` 放回）。"""

    global _PENDING
    taken, _PENDING = _PENDING, {}
    return taken


def requeue(rows: dict[tuple[str, str], Row]) -> None:
    for key, (count, first_at, last_at) in rows.items():
        row = _PENDING.get(key)
        if row is None:
            _PENDING[key] = [count, first_at, last_at]
        else:
            row[0] += count
            row[1] = min(row[1], first_at)
            row[2] = max(row[2], last_at)


def pending_snapshot() -> dict[tuple[str, str], tuple[int, float, float]]:
    return {key: (row[0], row[1], row[2]) for key, row in _PENDING.items()}


def set_on_hit(callback: Callable[[], None] | None) -> None:
    """设置命中回调（持久化层用它安排一次延迟写库）；回调必须足够轻。"""

    global _ON_HIT
    _ON_HIT = callback


def catalog() -> list[dict[str, Any]]:
    return [item.to_dict() for item in COMPAT_ITEMS]


def reset_for_test() -> None:
    _PENDING.clear()
    _STATE["dropped"] = 0


__all__ = [
    "COMPAT_INDEX",
    "COMPAT_ITEMS",
    "CompatItem",
    "DEPRECATION_TO_COMPAT",
    "HOST_PLUGIN_KEY",
    "catalog",
    "drain",
    "hit",
    "hit_deprecation",
    "pending_snapshot",
    "requeue",
    "reset_for_test",
    "set_on_hit",
]
