"""插件扩展点（``provides``）：宿主按能力选择插件，而不是按插件 key 写死（0.112.0 起，Plugin API 1.5）。

插件在 ``plugin.json`` 里声明自己实现了哪些扩展点::

    {"provides": ["ai.image_backend"]}

目前登记的扩展点：

- ``ai.image_backend``：``,ai image`` / 模式为 image 的 AI 指令模板的图片生成后端。插件需要注册至少一条命令
  （``@command``）；宿主把指令参数原样交给它的第一条命令（或模板里填写的 ``image_plugin_method``）。

兼容（旧插件 + 新服务器）：没有任何插件声明 ``ai.image_backend`` 时，宿主回退到
``app/plugin_compat.py`` 登记的 ``DEFAULT_IMAGE_BACKEND_PLUGIN``，行为与 0.110 完全一致；
模板里已保存的旧值（``image_backend`` 等于该插件 key）也继续固定走该插件（计划 Plugin API 2.0 移除）。

本模块只依赖标准库，主进程、worker 与插件库兼容检查脚本都可导入。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

AI_IMAGE_BACKEND = "ai.image_backend"

# 已登记的扩展点：名称 → 说明。未登记的名字在安装时给 lint 警告，运行时忽略。
EXTENSION_POINTS: dict[str, str] = {
    AI_IMAGE_BACKEND: "AI 指令图片生成后端（,ai image / image 模式模板）",
}

# 模板 config.image_backend 的取值。
IMAGE_BACKEND_PLUGIN = "plugin"  # 按能力自动选择声明了 ai.image_backend 的插件（0.112.0 起默认）
IMAGE_BACKEND_LLM = "llm"  # LLM Provider 原生生图


def normalize_provides(raw: Any) -> list[str]:
    """``provides`` 规范化：去空白、去重、保序；非列表返回空列表。"""

    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def unknown_provides(raw: Any) -> list[str]:
    """``provides`` 里宿主不认识的扩展点（安装 lint 用）。"""

    return [name for name in normalize_provides(raw) if name not in EXTENSION_POINTS]


def manifest_provides(manifest: Any) -> list[str]:
    return normalize_provides(getattr(manifest, "provides", None) if manifest is not None else None)


def select_image_backend_plugin(
    *,
    image_backend: str,
    image_plugin_key: str | None,
    providers: Iterable[str],
    legacy_default: str,
) -> tuple[str, str]:
    """决定 image 模式要调用的插件。返回 ``(plugin_key, reason)``。

    - ``image_plugin_key`` 显式填写：永远用它（与 0.110 相同）；
    - ``image_backend == legacy_default``（旧模板保存的历史插件 key）：固定走该插件（旧路径，行为不变）；
    - ``image_backend == "plugin"``（新默认）：取当前账号已加载、声明了 ``ai.image_backend`` 的第一个插件；
      一个都没有时回退 ``legacy_default``（旧插件 + 新服务器与 0.110 行为一致）。

    ``reason`` 取值 ``explicit`` / ``legacy`` / ``capability`` / ``fallback``，用于日志与提示文案。
    """

    explicit = str(image_plugin_key or "").strip()
    if explicit:
        return explicit, "explicit"
    if image_backend == legacy_default:
        return legacy_default, "legacy"
    for key in providers:
        key = str(key or "").strip()
        if key:
            return key, "capability"
    return legacy_default, "fallback"


__all__ = [
    "AI_IMAGE_BACKEND",
    "EXTENSION_POINTS",
    "IMAGE_BACKEND_LLM",
    "IMAGE_BACKEND_PLUGIN",
    "manifest_provides",
    "normalize_provides",
    "select_image_backend_plugin",
    "unknown_provides",
]
