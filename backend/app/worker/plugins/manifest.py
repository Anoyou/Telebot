"""插件 Manifest 数据类。

每个插件目录里的 ``manifest.py`` 顶层导出 ``MANIFEST: Manifest`` 实例，
loader 在扫描目录阶段读取这个常量来决定加载方式、显示名、版本以及（阶段 C 引入的）权限范围。

字段说明：
- ``key``：插件唯一 key（与 ``Plugin.key`` 一致；同时也是 ``feature.key``）
- ``display_name``：用户可见名称
- ``version``：语义化版本号；第三方插件通过 zip / 仓库升级时按此对比
- ``author``：作者；内置默认 ``"builtin"``
- ``description``：一句话描述（前端"插件管理"列表展示）
- ``requires_features``：声明依赖的其它插件 key 列表（先注册了才能加载本插件）
- ``min_telepilot_version``：声明最低 TelePilot 版本；旧 ``min_telebot_version`` 保留兼容
- ``config_schema``：``rule.config`` 的 JSON Schema，前端可据此生成动态表单
- ``category``：模块身份分类（interactive / automation / utility）
- ``interaction_entries``：声明可由交互 Bot 调用的交互入口
- ``experimental``：是否为实验性插件；前端可据此提示风险
- ``permissions``：阶段 C 沙箱用的能力声明（如 ``send_message`` / ``edit_message``）
- ``on_install``：可选的安装钩子模块路径（阶段 B/C 用，目前未启用）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Manifest:
    """插件元数据。"""

    key: str
    display_name: str
    version: str = "0.1.0"
    min_telepilot_version: str | None = None
    min_telebot_version: str | None = None
    author: str = "builtin"
    description: str = ""
    # 依赖其它插件的 key（先加载它们再加载本插件；阶段 A 暂不强制校验）
    requires_features: list[str] = field(default_factory=list)
    # rule.config 的 JSON Schema，可选；前端编辑器据此渲染
    config_schema: dict[str, Any] | None = None
    # 模块身份分类：interactive（互动娱乐）/ automation（自动化）/ utility（工具能力）
    category: str = "utility"
    # 可由交互 Bot 启动的入口声明；未声明则默认不出现在交互 Bot 模块列表里
    interaction_entries: list[dict[str, Any]] = field(default_factory=list)
    # 是否为实验性插件；前端可据此展示 warning badge
    experimental: bool = False
    # ===== 阶段 C 引入：能力清单 =====
    # 默认给三类常用能力，避免内置插件 manifest 漏写时被沙箱拦截
    permissions: list[str] = field(
        default_factory=lambda: ["send_message", "edit_message", "read_chat"]
    )
    # 可选：安装钩子（python module path），阶段 B/C 启用
    on_install: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化成可写入 DB / JSON 的 dict。"""
        return {
            "key": self.key,
            "display_name": self.display_name,
            "version": self.version,
            "min_telepilot_version": self.min_telepilot_version,
            "min_telebot_version": self.min_telebot_version,
            "author": self.author,
            "description": self.description,
            "requires_features": list(self.requires_features),
            "config_schema": self.config_schema,
            "category": self.category,
            "interaction_entries": list(self.interaction_entries),
            "x-experimental": self.experimental,
            "permissions": list(self.permissions),
            "on_install": self.on_install,
        }


__all__ = ["Manifest"]
