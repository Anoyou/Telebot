# TelePilot 插件概览

本文是当前维护的插件开发入口，统一说明插件路线、快速开始和基础目录结构。项目对外统一使用“插件”指代可安装、可启停、可配置的扩展能力；“模块化”只描述 TelePilot 的架构特色。

## 插件市场路线：Route A vs Route B

TelePilot 插件市场分两条路线推进，0.x 阶段明确选择 **Route A**：

- **Route A：受信/签名插件市场。** 仅接收 TelePilot/Anoyou 审核过的插件源，安装包需要签名或可信来源记录，插件在同一 worker 进程内运行，通过 `Manifest.permissions`、`ctx.client`、`ctx.http`、`ctx.ai` 等 facade 收口能力。它适合 0.x 快速稳定迭代，把重点放在插件 API、安装体验、权限声明、审计日志和回滚能力上。
- **Route B：开放社区市场。** 面向任意第三方上传或未经人工审核的插件，需要 subprocess/容器隔离、资源配额、文件系统/网络沙箱、供应链扫描和更完整的安全策略。它不属于 0.x 默认方案，若 1.0 之后开放社区市场，应作为独立 Epic 设计和验收。

因此，本文当前所有示例、CI 和安全边界都按 Route A 编写；不要把 Route A 的 facade 误读为零信任沙箱。

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
└── builtin/             # 内置插件
    ├── game24/
    └── forward/

plugins/installed/       # 远程/用户安装的插件
├── guess_number/
└── (更多插件...)
```

### 生命周期

```
loader._load_all()
  → scan builtin/ + plugins/installed/
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
