# TelePilot 插件概览

本文是当前维护的插件开发入口，统一说明插件路线、快速开始和基础目录结构。项目对外统一使用“插件”指代可安装、可启停、可配置的扩展能力；“模块化”只描述 TelePilot 的架构特色。

## 插件标准模式

TelePilot 0.x 阶段只保留一个默认插件模式：**个人可信插件标准模式**。

- 管理员安装并启用插件后，即视为信任该插件的业务逻辑；远程插件风险由管理员自行承担。
- 平台不做公共插件市场式强沙箱，但会通过 `Manifest.permissions`、`ctx.client`、`ctx.http`、`ctx.ai`、`ctx.messages` 等 facade 收口常用能力，并保留频控、审计、急停、日志脱敏和 token/session 隔离。
- 插件可以通过两类调度方式接入：管理员带前缀命令走 UserBot，群友关键词/付款开局走交互 Bot；涉及收款确认、发奖、补发等钱相关动作仍由 UserBot 或平台受控结算链路处理。

如果未来要开放“任意第三方上传、未经人工审核”的公共市场，需要另行设计 subprocess/容器隔离、资源配额、文件系统/网络沙箱和供应链扫描。它不属于当前 0.x 默认方案；本文当前所有示例、CI 和安全边界都按个人可信插件标准模式编写。

---

## 1. 快速开始

### 文件结构

```
plugins/installed/{插件名}/
├── __init__.py        # 导出 PLUGIN_CLASS 和 MANIFEST
├── manifest.py        # Manifest 元数据
├── plugin.py          # 插件主类
└── (其他插件)
```

### 最小可运行插件

**plugin.py：**
```python
from app.worker.plugins.base import Plugin, register

@register
class PingPlugin(Plugin):
    key = "ping"
    display_name = "Ping"

    async def on_command(self, ctx, cmd, args, event) -> bool:
        if cmd == "ping":
            await event.edit("pong")
            return True
        return False
```

**manifest.py：**
```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="ping",
    display_name="Ping",
    version="0.1.0",
    author="example",
    description="响应 ping 指令",
    permissions=["edit_message"],
)
```

**__init__.py：**
```python
from .manifest import MANIFEST
from .plugin import PingPlugin

PLUGIN_CLASS = PingPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

通过安装接口安装并在账号上启用后，worker 会在授权检查通过时加载它。仅手工拷贝到 `plugins/installed/ping/` 的目录会被标记为孤立目录（orphan）并拒绝加载。

---

## 2. 插件结构（Plugin 包）

### 目录约定

```
backend/app/worker/plugins/
├── base.py              # Plugin 基类 + register 装饰器
├── manifest.py          # Manifest 数据类
├── loader.py            # 插件加载器 + 热重载 + generation guard
├── builtin/             # 核心平台/兼容代码，普通插件不要放这里
│   ├── scheduler/        # 平台调度兼容壳，实际由 PlatformScheduler 执行
│   └── forward/
└── official/            # TelePilot 随包官方可选插件源，只用于 Web 安装

plugins/installed/       # 远程/本地/官方可选安装后的运行目录
├── guess_number/
└── (更多插件...)
```

`backend/app/worker/plugins/builtin/` 中可能保留旧版本兼容目录，但扫描器只把核心平台能力纳入 builtin registry。`auto_reply`、`autorepeat`、`chatgpt_image`、`codex_image`、`game24`、`math10` 从 0.35 起走官方可选插件库：Web 安装后复制到 `plugins/installed/{key}/`，再按安装型插件加载。

### 生命周期

```
loader._load_all()
  → scan 核心 builtin/ + plugins/installed/
  → import plugin.py + manifest.py
  → 验证 Manifest 合法性
  → 实例化 Plugin 子类
  → 调用 on_startup(ctx)

热重载 (reload_plugin):
  → state.generation += 1          # generation guard
  → 旧插件: on_shutdown(ctx)
  → 重新 import + 实例化
  → 新插件: on_startup(ctx)

消息派发:
  → 检查 ctx.generation == state.generation
  → 跳过过期 handler（竞态保护）
  → 调用 on_command / on_message
```

---
